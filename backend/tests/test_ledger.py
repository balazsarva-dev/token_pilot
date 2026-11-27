"""Import, idempotency, pricing and reconciliation against a real Postgres."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from tests import fixtures
from tokenpilot.ledger import reports
from tokenpilot.ledger.catalog import seed_catalog
from tokenpilot.ledger.importer import import_csv
from tokenpilot.ledger.models import DimApiKey, DimModel, FctBilledCost, FctUsage

TENANT = "t1"


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_import_prices_usage_and_reconciles_with_the_bill(session):
    seed_catalog(session)

    usage = import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())
    cost = import_csv(session, TENANT, fixtures.OPENAI_COST.encode())

    assert (usage.usage_rows, usage.unpriced_rows, usage.skipped_empty) == (1, 0, 1)
    assert cost.cost_rows == 4
    [row] = reports.reconciliation(session, TENANT)
    assert row["computed_usd"] == Decimal("0.0282500000")
    assert row["billed_usd"] == Decimal("0.0282500000")  # tool calls excluded: not token usage
    assert row["diff_usd"] == 0


def test_same_file_twice_is_imported_once(session):
    first = import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())
    second = import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())

    assert second.already_imported and second.batch_id == first.batch_id
    assert _count(session, FctUsage) == 1


def test_overlapping_exports_update_instead_of_doubling(session):
    import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())
    # A later export of the same day (different bytes, corrected numbers).
    corrected = fixtures.OPENAI_USAGE.replace("12686.0,0.0", "20000.0,0.0", 1).replace(
        "11512.0,12686.0,0.0,0.0", "11512.0,20000.0,0.0,0.0", 1
    )
    import_csv(session, TENANT, (corrected + "\n").encode())

    assert _count(session, FctUsage) == 1
    assert session.scalar(select(FctUsage.output)) == 20000


def test_same_file_for_another_tenant_is_separate(session):
    import_csv(session, "a", fixtures.OPENAI_USAGE.encode())
    import_csv(session, "b", fixtures.OPENAI_USAGE.encode())

    assert _count(session, FctUsage) == 2


def test_rows_of_one_grain_are_summed_and_unpriced_tiers_stay_visible(session):
    seed_catalog(session)

    result = import_csv(session, TENANT, fixtures.ANTHROPIC_USAGE.encode())

    assert result.usage_rows == 2  # two context-window rows collapsed into one grain
    sonnet5 = session.scalar(select(FctUsage).where(FctUsage.pricing_tier == "standard"))
    assert sonnet5.input_uncached == 2500
    assert sonnet5.computed_cost == Decimal("0.0153200000")  # (2500*2 + 1032*10) / 1M
    fast = session.scalar(select(FctUsage).where(FctUsage.pricing_tier == "fast"))
    assert fast.computed_cost is None  # fast mode is not priced in v1 — never 0


def test_unknown_model_is_kept_unpriced_then_merged_when_added_to_the_catalog(session, tmp_path):
    result = import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())  # no catalog yet

    assert result.unmapped_models == ["gpt-5-mini-2025-08-07"]
    assert result.unpriced_rows == 1

    seed = seed_catalog(session)

    assert seed.merged_unmapped == 1 and seed.repriced_rows == 1
    row = session.scalar(select(FctUsage))
    model = session.get(DimModel, row.model_key)
    assert (model.model_id, model.is_mapped) == ("gpt-5-mini", True)
    assert row.computed_cost == Decimal("0.0282500000")
    assert reports.unmapped_models(session) == []


def test_price_versions_close_each_other_and_price_by_day(session, tmp_path):
    catalog = Path(tmp_path, "prices.toml")
    catalog.write_text(
        """
[[model]]
provider = "openai"
model_id = "gpt-5-mini"
aliases = ["gpt-5-mini-2025-08-07"]
  [[model.price]]
  valid_from = 2026-09-01
  input_uncached = "1"
  output = "1"
  source = "test"
  [[model.price]]
  valid_from = 2026-09-20
  input_uncached = "2"
  output = "2"
  source = "test"
"""
    )
    seed_catalog(session, catalog)
    import_csv(session, TENANT, fixtures.OPENAI_USAGE.encode())  # usage on 2026-09-20

    cost = session.scalar(select(FctUsage.computed_cost))
    assert cost == Decimal("0.0483960000")  # (11512 + 12686) * 2 / 1M: the newer price applies


def test_api_key_dimension_is_enriched_across_exports(session):
    import_csv(session, TENANT, fixtures.ANTHROPIC_USAGE.encode())  # names only
    import_csv(session, TENANT, fixtures.ANTHROPIC_COST.encode())  # names + id + status

    [key] = session.scalars(select(DimApiKey)).all()
    assert (key.name, key.key_id, key.status) == ("key-one", "apikey_01ONE", "active")
    assert str(key.first_seen) == "2026-09-21" and str(key.last_seen) == "2026-09-22"
    assert _count(session, FctBilledCost) == 1
