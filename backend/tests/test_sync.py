"""API sync: provider adapters against recorded-shape responses (fake ids), and the sync job.

Response shapes mirror what the real admin APIs returned in October 2026; no network here.
"""

import json
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select

from tests import fixtures
from tokenpilot.config import Settings
from tokenpilot.ledger import reports
from tokenpilot.ledger.catalog import seed_catalog
from tokenpilot.ledger.importer import import_csv
from tokenpilot.ledger.models import DimApiKey, FctBilledCost, FctUsage
from tokenpilot.sources import anthropic_api, openai_api
from tokenpilot.sources.http import SyncError
from tokenpilot.sync import sync, sync_window, synced_through

DAY = 1789862400  # 2026-09-20 00:00 UTC
FAKE_OPENAI_KEY = "sk-admin-test-not-real"


class FakeApi:
    """Routes (host + path) to queued JSON responses and records every request."""

    def __init__(self, routes: dict[str, list]):
        self.routes = routes
        self.calls: list[tuple[str, dict[str, list[str]], dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]):
        parts = urlsplit(url)
        self.calls.append((parts.path, parse_qs(parts.query), headers))
        queue = self.routes[parts.netloc + parts.path]
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        status, body, response_headers = (
            response if isinstance(response, tuple) else (200, response, {})
        )
        return status, response_headers, json.dumps(body).encode()


def _openai_usage(start: int, model: str, key: str, batch: bool, tokens: tuple[int, int]):
    return {
        "object": "bucket",
        "start_time": start,
        "end_time": start + 86400,
        "results": [
            {
                "object": "organization.usage.completions.result",
                "project_id": "proj_A",
                "num_model_requests": 13,
                "user_id": None,
                "api_key_id": key,
                "model": model,
                "batch": batch,
                "service_tier": "default",
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "input_cached_tokens": 0,
                "input_cache_write_tokens": 0,
                "input_cache_write_30m_tokens": 0,
                "input_cache_write_12h_tokens": 0,
                "input_uncached_tokens": tokens[0],
                "input_audio_tokens": 0,
                "input_cached_audio_tokens": 0,
                "output_audio_tokens": 0,
                "api_source": None,
            }
        ],
    }


def _openai_cost(line_item: str, value: float):
    return {
        "object": "organization.costs.result",
        "amount": {"currency": "usd", "value": value},
        "api_key_id": None,
        "line_item": line_item,
        "project_id": "proj_A",
        "project_name": "Project A",
        "quantity": 0.0,
    }


def openai_routes() -> dict[str, list]:
    host = "api.openai.com/v1/organization"
    return {
        f"{host}/usage/completions": [
            {
                "data": [
                    _openai_usage(DAY, "gpt-5-mini-2025-08-07", "key_A", False, (11512, 12686))
                ],
                "has_more": True,
                "next_page": "page_2",
            },
            {
                "data": [
                    _openai_usage(DAY, "gpt-5-mini-2025-08-07", "key_B", True, (1000, 100)),
                    {"start_time": DAY + 86400, "end_time": DAY + 2 * 86400, "results": []},
                ],
                "has_more": False,
                "next_page": None,
            },
        ],
        f"{host}/costs": [
            {
                "data": [
                    {
                        "start_time": DAY,
                        "end_time": DAY + 86400,
                        "results": [
                            _openai_cost("gpt-5-mini-2025-08-07, cached input", 0.0),
                            _openai_cost("gpt-5-mini-2025-08-07, input", 0.002878),
                            _openai_cost("gpt-5-mini-2025-08-07, output", 0.025372),
                        ],
                    }
                ],
                "has_more": False,
                "next_page": None,
            }
        ],
        f"{host}/projects": [{"data": [{"id": "proj_A", "name": "Project A"}], "has_more": False}],
        f"{host}/projects/proj_A/api_keys": [
            {"data": [{"id": "key_A", "name": "prod-summarizer"}], "has_more": False}
        ],
    }


def anthropic_routes() -> dict[str, list]:
    host = "api.anthropic.com/v1/organizations"
    bucket = {"starting_at": "2026-09-20T00:00:00Z", "ending_at": "2026-09-21T00:00:00Z"}
    usage = {
        "uncached_input_tokens": 10545,
        "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
        "cache_read_input_tokens": 0,
        "output_tokens": 2353,
        "server_tool_use": {"web_search_requests": 0},
        "api_key_id": "apikey_01",
        "workspace_id": None,
        "model": "claude-sonnet-5",
        "service_tier": "standard",
        "speed": None,
    }

    def cost(amount: str, token_type: str):
        return {
            "currency": "USD",
            "amount": amount,
            "workspace_id": None,
            "description": "Claude Sonnet 5",
            "cost_type": "tokens",
            "model": "claude-sonnet-5",
            "service_tier": "standard",
            "token_type": token_type,
        }

    return {
        f"{host}/usage_report/messages": [
            {"data": [{**bucket, "results": [usage]}], "has_more": False, "next_page": None}
        ],
        f"{host}/cost_report": [
            {
                "data": [
                    {
                        **bucket,
                        "results": [
                            cost("2.109", "uncached_input_tokens"),
                            cost("2.353", "output_tokens"),
                            cost("0", "cache_read_input_tokens"),
                            cost("1.5", "brand_new_token_type"),
                        ],
                    }
                ],
                "has_more": False,
                "next_page": None,
            }
        ],
        f"{host}/api_keys": [
            {
                "data": [{"id": "apikey_01", "name": "my-portfolio-api", "status": "archived"}],
                "has_more": False,
            }
        ],
        f"{host}/workspaces": [{"data": [], "has_more": False}],
    }


def test_openai_fetch_follows_pages_and_adds_names():
    api = FakeApi(openai_routes())

    parsed = openai_api.fetch(
        openai_api.client(FAKE_OPENAI_KEY, api), date(2026, 9, 20), date(2026, 9, 21)
    )

    assert [(r.api_key.natural_id, r.api_key.name, r.pricing_tier) for r in parsed.usage] == [
        ("key_A", "prod-summarizer", "standard"),
        ("key_B", None, "batch"),  # key no longer listed: the id stays the label
    ]
    assert parsed.usage[0].scope.name == "Project A"
    assert parsed.usage[0].requests == 13
    assert sorted(r.amount_usd for r in parsed.costs) == [
        0,
        Decimal("0.002878"),
        Decimal("0.025372"),
    ]
    usage_calls = [c for c in api.calls if c[0].endswith("/usage/completions")]
    assert usage_calls[1][1]["page"] == ["page_2"]
    assert usage_calls[0][1]["end_time"] == [str(DAY + 2 * 86400)]  # end day inclusive
    assert all(c[2]["Authorization"] == f"Bearer {FAKE_OPENAI_KEY}" for c in api.calls)


def test_anthropic_fetch_converts_cents_and_matches_export_names():
    api = FakeApi(anthropic_routes())

    parsed = anthropic_api.fetch(
        anthropic_api.client("sk-ant-admin-test", api), date(2026, 9, 20), date(2026, 9, 20)
    )

    (usage,) = parsed.usage
    # Same natural ids as the Console exports (key name, "Default" workspace).
    assert (usage.api_key.natural_id, usage.api_key.key_id, usage.api_key.status) == (
        "my-portfolio-api",
        "apikey_01",
        "archived",
    )
    assert usage.scope.natural_id == "Default"
    assert (usage.tokens.input_uncached, usage.tokens.output, usage.requests) == (10545, 2353, None)
    assert [(c.token_type, c.amount_usd) for c in parsed.costs] == [
        ("input_uncached", Decimal("0.02109")),
        ("output", Decimal("0.02353")),
    ]
    assert parsed.skipped_empty == 1  # the zero-amount row
    assert [r.reason for r in parsed.rejected] == [
        "cost: unknown token_type 'brand_new_token_type'"
    ]


def test_client_waits_on_rate_limits_and_explains_rejected_keys():
    waits = []
    api = FakeApi(
        {
            "api.openai.com/v1/organization/projects": [
                (429, {"error": {"message": "slow down"}}, {"Retry-After": "7"}),
                {"data": [], "has_more": False},
            ]
        }
    )
    client = openai_api.client(FAKE_OPENAI_KEY, api, sleep=waits.append)
    assert client.get("/organization/projects") == {"data": [], "has_more": False}
    assert waits == [7.0]

    denied = FakeApi(
        {"api.openai.com/v1/organization/projects": [(401, {"error": {"message": "bad key"}}, {})]}
    )
    with pytest.raises(SyncError) as exc:
        openai_api.client(FAKE_OPENAI_KEY, denied, sleep=waits.append).get("/organization/projects")
    assert "OPENAI_ADMIN_KEY" in str(exc.value) and "bad key" in str(exc.value)
    assert FAKE_OPENAI_KEY not in str(exc.value)


def test_sync_window():
    today = date(2026, 10, 20)
    assert sync_window(None, today) == (date(2026, 9, 20), date(2026, 10, 19))  # first: 30 days
    assert sync_window(date(2026, 10, 15), today) == (date(2026, 10, 13), date(2026, 10, 19))
    assert sync_window(date(2026, 10, 15), today, days=7) == (
        date(2026, 10, 13),
        date(2026, 10, 19),
    )
    assert sync_window(None, today, days=10_000)[0] == date(2025, 10, 20)  # at most a year


def _billed_and_tokens(session, provider: str):
    billed = session.scalar(
        select(func.sum(FctBilledCost.amount_usd)).where(FctBilledCost.provider == provider)
    )
    output = session.scalar(select(func.sum(FctUsage.output)).where(FctUsage.provider == provider))
    return billed, output


def test_sync_replaces_csv_days_instead_of_adding_to_them(session):
    seed_catalog(session)
    import_csv(session, "local", fixtures.OPENAI_USAGE.encode())
    import_csv(session, "local", fixtures.OPENAI_COST.encode())  # includes a $0.005 tool line
    settings = Settings(database_url="unused", openai_admin_key=FAKE_OPENAI_KEY)

    (result,) = sync(
        session, "local", settings, today=date(2026, 9, 22), transport=FakeApi(openai_routes())
    )

    # Restarts 3 days before the newest day already in the ledger (the CSV's 2026-09-20).
    assert (result.ok, result.start, result.end) == (True, date(2026, 9, 18), date(2026, 9, 21))
    # The API's cost rows replace the CSV's for the synced days: no double counting.
    assert _billed_and_tokens(session, "openai") == (Decimal("0.028250"), 12686 + 100)

    # The same window with the same data again: nothing to do.
    again = sync(
        session,
        "local",
        settings,
        days=4,
        today=date(2026, 9, 22),
        transport=FakeApi(openai_routes()),
    )
    assert (again[0].start, again[0].unchanged) == (date(2026, 9, 18), True)


def test_one_provider_failing_does_not_stop_the_other(session):
    seed_catalog(session)
    routes = anthropic_routes() | {
        "api.openai.com/v1/organization/usage/completions": [(401, {"error": "nope"}, {})]
    }
    settings = Settings(
        database_url="unused", openai_admin_key=FAKE_OPENAI_KEY, anthropic_admin_key="sk-ant-x"
    )

    results = sync(session, "local", settings, today=date(2026, 9, 21), transport=FakeApi(routes))

    assert [(r.provider, r.ok) for r in results] == [("openai", False), ("anthropic", True)]
    assert "rejected (HTTP 401)" in results[0].error
    assert _billed_and_tokens(session, "anthropic") == (Decimal("0.04462"), 2353)


def test_sync_needs_a_key(session):
    with pytest.raises(SyncError, match="No admin key configured for anthropic"):
        sync(session, "local", Settings(database_url="unused"), providers=["anthropic"])


def test_sync_endpoints(session, client):
    status = client.get("/api/spend/sync").get_json()
    assert status == {
        "providers": [
            {"provider": "openai", "configured": False, "synced_through": None},
            {"provider": "anthropic", "configured": False, "synced_through": None},
        ]
    }
    response = client.post("/api/spend/sync")
    assert response.status_code == 400
    assert response.get_json()["error"]["type"] == "not_configured"
    response = client.post("/api/spend/sync?tenant=demo")
    assert response.get_json()["error"]["type"] == "demo_tenant"


def test_sync_updates_known_keys_and_remembers_the_days_it_covered(session):
    seed_catalog(session)
    import_csv(session, "local", fixtures.ANTHROPIC_USAGE.encode())  # key-one, Sept 21-22
    empty = {"data": [], "has_more": False, "next_page": None}
    routes = anthropic_routes() | {
        "api.anthropic.com/v1/organizations/usage_report/messages": [empty],
        "api.anthropic.com/v1/organizations/cost_report": [empty],
        "api.anthropic.com/v1/organizations/api_keys": [
            {
                "data": [{"id": "apikey_09", "name": "key-one", "status": "archived"}],
                "has_more": False,
            }
        ],
    }
    settings = Settings(database_url="unused", anthropic_admin_key="sk-ant-x")

    sync(session, "local", settings, days=1, today=date(2026, 10, 20), transport=FakeApi(routes))

    key = session.scalar(select(DimApiKey).where(DimApiKey.natural_id == "key-one"))
    assert (key.key_id, key.status) == ("apikey_09", "archived")  # unused in the window
    # No usage on Oct 19, but the sync covered it: the next one starts from there.
    assert synced_through(session, "local", "anthropic") == date(2026, 10, 19)


def test_reconciliation_leaves_out_days_with_unpriced_usage(session):
    seed_catalog(session)
    settings = Settings(database_url="unused", anthropic_admin_key="sk-ant-x")
    sync(
        session,
        "local",
        settings,
        days=1,
        today=date(2026, 9, 21),
        transport=FakeApi(anthropic_routes()),
    )
    (exact,) = reports.reconciliation_summary(session, "local")
    assert (exact["days_compared"], exact["days_unpriced"], exact["diff_pct"]) == (1, 0, 0)

    routes = anthropic_routes()
    page = routes["api.anthropic.com/v1/organizations/usage_report/messages"][0]
    unknown = {**page["data"][0]["results"][0], "model": "claude-not-in-catalog"}
    page["data"][0]["results"].append(unknown)
    sync(session, "local", settings, days=1, today=date(2026, 9, 21), transport=FakeApi(routes))
    (partial,) = reports.reconciliation_summary(session, "local")
    # The computed side would miss the unknown model's cost: not a fair comparison.
    assert (partial["days_compared"], partial["days_unpriced"], partial["diff_pct"]) == (0, 1, None)
