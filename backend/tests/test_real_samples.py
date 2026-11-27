"""Optional check on your own real exports in test_ai_costs/ (git-ignored). Skipped when they are not present."""

from pathlib import Path

import pytest

from tokenpilot.ledger import reports
from tokenpilot.ledger.catalog import seed_catalog
from tokenpilot.ledger.importer import import_csv

SAMPLES = Path(__file__).resolve().parents[2] / "test_ai_costs"

pytestmark = pytest.mark.skipif(not SAMPLES.is_dir(), reason="real sample exports not present")


def test_real_exports_import_cleanly_and_openai_reconciles_exactly(session):
    seed_catalog(session)

    for path in sorted(SAMPLES.glob("*.csv")):
        result = import_csv(session, "real", path.read_bytes())
        assert result.rejected == [], path.name
        assert result.unmapped_models == [], path.name

    openai_days = [r for r in reports.reconciliation(session, "real") if r["provider"] == "openai"]
    assert openai_days
    assert all(r["diff_usd"] == 0 for r in openai_days)
