"""The audit end to end: ledger in Postgres -> rules -> report, and the HTTP API."""

import io
from datetime import date
from decimal import Decimal

from tests import fixtures
from tokenpilot.audit import build_audit
from tokenpilot.demo import DEMO_TENANT, build_demo
from tokenpilot.ledger.catalog import seed_catalog
from tokenpilot.ledger.importer import import_parsed

DEMO_END = date(2026, 10, 19)


def _load_demo(session):
    seed_catalog(session)
    for parsed, source_id in build_demo(DEMO_END):
        import_parsed(session, DEMO_TENANT, parsed, source_id)


def test_demo_audit_finds_every_planted_scenario(session):
    _load_demo(session)

    report = build_audit(session, DEMO_TENANT)

    assert report["period"] == {"start": date(2026, 9, 20), "end": DEMO_END, "days": 30}
    found = {(f["rule"], f["subject"].get("key")) for f in report["findings"]}
    assert found >= {
        ("right_sizing", "support-bot-prod"),
        ("caching", "nightly-reports"),
        ("batch", "nightly-reports"),
        ("caching", "assistant-prod"),
        ("anomaly", "assistant-prod"),
        ("idle_key", "prototype-2025"),
    }
    # Interactive traffic (drops on weekends) gets no batch advice; evals already use batch.
    batch_keys = {f["subject"]["key"] for f in report["findings"] if f["rule"] == "batch"}
    assert batch_keys == {"nightly-reports"}
    assert round(report["savings"]["likely_monthly_usd"], 2) == Decimal("842.82")
    assert report["savings"]["potential_monthly_usd"] > report["savings"]["likely_monthly_usd"]


def test_audit_period_limits_the_data(session):
    _load_demo(session)

    report = build_audit(session, DEMO_TENANT, date(2026, 10, 13), DEMO_END)

    assert report["period"]["days"] == 7
    assert any("Only 7 days of data" in note for note in report["data_quality"]["notes"])


def test_audit_without_data_is_none(session):
    assert build_audit(session, "nobody") is None


# ---------------------------------------------------------------- HTTP API


def test_upload_reports_each_file_and_keeps_going_after_a_bad_one(session, client):
    seed_catalog(session)
    files = [
        (io.BytesIO(fixtures.OPENAI_USAGE.encode()), "usage.csv"),
        (io.BytesIO(b"just,some,columns\n1,2,3\n"), "other.csv"),
    ]

    response = client.post("/api/spend/imports?tenant=t1", data={"files": files})

    assert response.status_code == 200
    good, bad = response.json["results"]
    assert (good["ok"], good["provider"], good["usage_rows"]) == (True, "openai", 1)
    assert bad["ok"] is False and bad["error"]


def test_audit_endpoint_returns_money_as_strings(session, client):
    _load_demo(session)

    response = client.get(f"/api/spend/audit?tenant={DEMO_TENANT}")

    assert response.status_code == 200
    body = response.json
    assert body["period"]["start"] == "2026-09-20"
    assert isinstance(body["savings"]["likely_monthly_usd"], str)
    assert Decimal(body["savings"]["likely_monthly_usd"]) > 0
    assert body["findings"][0]["kind"] == "saving"


def test_audit_endpoint_errors_use_one_envelope(session, client):
    no_data = client.get("/api/spend/audit?tenant=nobody")
    bad_date = client.get("/api/spend/audit?from=2026-13-01")
    bad_tenant = client.get("/api/spend/audit?tenant=Robert'); DROP")
    not_found = client.get("/api/spend/nothing-here")

    assert (no_data.status_code, no_data.json["error"]["type"]) == (404, "no_data")
    assert (bad_date.status_code, bad_date.json["error"]["type"]) == (400, "bad_date")
    assert (bad_tenant.status_code, bad_tenant.json["error"]["type"]) == (400, "bad_tenant")
    assert not_found.status_code == 404 and "error" in not_found.json


def test_delete_data_starts_over(session, client):
    _load_demo(session)

    assert client.delete(f"/api/spend/data?tenant={DEMO_TENANT}").status_code == 204
    assert client.get(f"/api/spend/audit?tenant={DEMO_TENANT}").status_code == 404


def test_demo_has_no_write_endpoint_and_stays_apart_from_local(session, client):
    _load_demo(session)

    # The example data is generated from the CLI only (make demo), never over HTTP.
    assert client.post("/api/spend/demo").status_code in (404, 405)
    body = client.get(f"/api/spend/audit?tenant={DEMO_TENANT}").json
    assert Decimal(body["spend"]["monthly_after_likely_usd"]) == Decimal(
        body["spend"]["monthly_run_rate_usd"]
    ) - Decimal(body["savings"]["likely_monthly_usd"])
    assert client.get("/api/spend/audit?tenant=local").status_code == 404


def test_daily_endpoint_lists_spend_per_day(session, client):
    _load_demo(session)

    body = client.get(f"/api/spend/daily?tenant={DEMO_TENANT}&from=2026-10-13").json

    days = body["days"]
    assert [d["date"] for d in days] == [f"2026-10-{n}" for n in range(13, 20)]
    assert all(isinstance(d["computed_usd"], str) for d in days)
    report = build_audit(session, DEMO_TENANT, date(2026, 10, 13), DEMO_END)
    assert sum(Decimal(d["computed_usd"]) for d in days) == report["spend"]["total_usd"]
