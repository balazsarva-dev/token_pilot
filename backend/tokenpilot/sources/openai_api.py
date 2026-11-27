"""OpenAI admin API: organization usage (completions) and costs, 1-day buckets.

The API returns the same fields as the Usage-page exports, so each result is turned into
an export-like row and parsed by openai_csv: one set of rules for both inputs. The API
adds what the exports lack: project and API key names (looked up once per sync).
"""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from tokenpilot.sources import openai_csv
from tokenpilot.sources.http import JsonClient, Transport, urllib_transport
from tokenpilot.sources.records import KeyRef, ParsedFile, RejectedRow

PROVIDER = "openai"
BASE_URL = "https://api.openai.com/v1"
MAX_DAILY_BUCKETS = 31  # per page, for bucket_width=1d


def client(admin_key: str, transport: Transport = urllib_transport, **kwargs) -> JsonClient:
    headers = {"Authorization": f"Bearer {admin_key}", "Accept": "application/json"}
    return JsonClient("OpenAI", BASE_URL, headers, "OPENAI_ADMIN_KEY", transport, **kwargs)


def fetch(api: JsonClient, start: date, end: date) -> ParsedFile:
    """Usage and billed cost for the days start..end (inclusive, UTC)."""
    window = [("start_time", _unix(start)), ("end_time", _unix(end + timedelta(days=1)))]
    usage_rows = _rows(
        api,
        "/organization/usage/completions",
        window
        + [("bucket_width", "1d"), ("limit", MAX_DAILY_BUCKETS)]
        + [("group_by", g) for g in ("project_id", "api_key_id", "model", "batch", "service_tier")],
    )
    cost_rows = _rows(
        api,
        "/organization/costs",
        window
        + [("bucket_width", "1d"), ("limit", MAX_DAILY_BUCKETS)]
        + [("group_by", g) for g in ("project_id", "line_item")],
    )
    usage = openai_csv.parse_usage(usage_rows)
    costs = openai_csv.parse_cost(cost_rows)

    projects = _project_names(api)
    keys = _key_names(api, projects)
    parsed = ParsedFile(source_type="openai_api", provider=PROVIDER)
    parsed.usage = [
        replace(
            r,
            api_key=replace(r.api_key, name=keys.get(r.api_key.natural_id)),
            scope=replace(r.scope, name=projects.get(r.scope.natural_id)),
        )
        for r in usage.usage
    ]
    parsed.costs = [
        replace(r, scope=replace(r.scope, name=r.scope.name or projects.get(r.scope.natural_id)))
        for r in costs.costs
    ]
    parsed.known_keys = [KeyRef(natural_id=i, key_id=i, name=name) for i, name in keys.items()]
    parsed.rejected = [RejectedRow(x.line, f"usage: {x.reason}") for x in usage.rejected] + [
        RejectedRow(x.line, f"cost: {x.reason}") for x in costs.rejected
    ]
    parsed.skipped_empty = usage.skipped_empty + costs.skipped_empty
    return parsed


def _rows(api: JsonClient, path: str, params: list[tuple[str, object]]) -> list[dict[str, str]]:
    """Flatten buckets into export-like rows (all values as strings, like a CSV cell)."""
    rows = []
    for page in api.pages(path, params):
        for bucket in page.get("data", []):
            for result in bucket.get("results", []):
                row = {k: _cell(v) for k, v in result.items() if not isinstance(v, dict)}
                if isinstance(result.get("amount"), dict):
                    row["amount_value"] = _cell(result["amount"].get("value"))
                    row["amount_currency"] = _cell(result["amount"].get("currency"))
                row["start_time"] = _cell(bucket.get("start_time"))
                row["end_time"] = _cell(bucket.get("end_time"))
                rows.append(row)
    return rows


def _project_names(api: JsonClient) -> dict[str, str]:
    projects = _list(api, "/organization/projects", [("include_archived", "true")])
    return {p["id"]: p["name"] for p in projects if p.get("name")}


def _key_names(api: JsonClient, projects: dict[str, str]) -> dict[str, str]:
    """Key names live per project (one request per project)."""
    return {
        k["id"]: k["name"]
        for project_id in projects
        for k in _list(api, f"/organization/projects/{project_id}/api_keys")
        if k.get("name")
    }


def _list(api: JsonClient, path: str, extra: list[tuple[str, object]] | None = None) -> list[dict]:
    """Paginated list endpoint (after / last_id cursor)."""
    items: list[dict] = []
    after = None
    while True:
        params = [("limit", 100), *(extra or [])] + ([("after", after)] if after else [])
        body = api.get(path, params)
        items.extend(body.get("data", []))
        after = body.get("last_id")
        if not body.get("has_more") or not after:
            return items


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _unix(day: date) -> int:
    return int(datetime.combine(day, time(), tzinfo=UTC).timestamp())
