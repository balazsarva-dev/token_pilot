"""Read-side queries over the ledger. Returns plain dicts, so CLI, API and tests can share them.

Optional start/end (inclusive dates) limit a query to a period; None = no limit.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import Session

from tokenpilot.ledger.dimensions import date_key_of
from tokenpilot.ledger.models import DimApiKey, DimModel, DimScope, FctUsage

GROUPINGS = {
    "model": (DimModel.provider, func.coalesce(DimModel.display_name, DimModel.model_id)),
    "key": (DimApiKey.provider, func.coalesce(DimApiKey.name, DimApiKey.natural_id)),
    "scope": (DimScope.provider, func.coalesce(DimScope.name, DimScope.natural_id)),
}


def spend_by(
    session: Session,
    tenant_id: str,
    grouping: str,
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    """Computed spend and tokens grouped by model, key or scope, most expensive first."""
    provider_col, label_col = GROUPINGS[grouping]
    total_input = FctUsage.input_uncached + FctUsage.input_cached
    query = (
        select(
            provider_col.label("provider"),
            label_col.label("label"),
            func.sum(FctUsage.computed_cost).label("computed_usd"),
            func.sum(total_input).label("input_tokens"),
            func.sum(FctUsage.output).label("output_tokens"),
            func.count().filter(FctUsage.computed_cost.is_(None)).label("unpriced_rows"),
        )
        .join(DimModel, DimModel.model_key == FctUsage.model_key)
        .join(DimApiKey, DimApiKey.api_key_key == FctUsage.api_key_key)
        .join(DimScope, DimScope.scope_key == FctUsage.scope_key)
        .where(FctUsage.tenant_id == tenant_id, *_in_period(FctUsage.date_key, start, end))
        .group_by(provider_col, label_col)
        .order_by(func.sum(FctUsage.computed_cost).desc().nulls_last())
    )
    return [_as_dict(r) for r in session.execute(query)]


def daily_spend(
    session: Session, tenant_id: str, start: date | None = None, end: date | None = None
) -> list[dict]:
    """Computed spend per day with usage, oldest first (days without usage are left out)."""
    query = (
        select(FctUsage.date_key, func.sum(FctUsage.computed_cost).label("computed_usd"))
        .where(FctUsage.tenant_id == tenant_id, *_in_period(FctUsage.date_key, start, end))
        .group_by(FctUsage.date_key)
        .order_by(FctUsage.date_key)
    )
    return [
        {"date": _date_of(key), "computed_usd": usd or Decimal(0)}
        for key, usd in session.execute(query)
    ]


def reconciliation(
    session: Session, tenant_id: str, start: date | None = None, end: date | None = None
) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT provider, date, computed_usd, billed_usd, diff_usd, diff_pct, unpriced_rows "
            "FROM daily_reconciliation WHERE tenant_id = :tenant "
            "AND (CAST(:start AS date) IS NULL OR date >= :start) "
            "AND (CAST(:end AS date) IS NULL OR date <= :end) "
            "ORDER BY provider, date"
        ),
        {"tenant": tenant_id, "start": start, "end": end},
    )
    return [_as_dict(r) for r in rows]


def reconciliation_summary(
    session: Session, tenant_id: str, start: date | None = None, end: date | None = None
) -> list[dict]:
    """Per provider: computed vs billed over the days where both exist.

    Days with unpriced usage are left out (their computed cost is partial) and counted.
    """
    rows = session.execute(
        text(
            "SELECT provider, "
            "  COUNT(*) FILTER (WHERE comparable) AS days_compared, "
            "  COUNT(*) FILTER (WHERE billed_usd IS NULL) AS days_without_bill, "
            "  COUNT(*) FILTER (WHERE billed_usd IS NOT NULL AND unpriced_rows > 0) "
            "    AS days_unpriced, "
            "  SUM(computed_usd) FILTER (WHERE comparable) AS computed_usd, "
            "  SUM(billed_usd) FILTER (WHERE comparable) AS billed_usd "
            "FROM (SELECT *, computed_usd IS NOT NULL AND billed_usd IS NOT NULL "
            "        AND unpriced_rows = 0 AS comparable "
            "      FROM daily_reconciliation WHERE tenant_id = :tenant "
            "      AND (CAST(:start AS date) IS NULL OR date >= :start) "
            "      AND (CAST(:end AS date) IS NULL OR date <= :end)) AS d "
            "GROUP BY provider ORDER BY provider"
        ),
        {"tenant": tenant_id, "start": start, "end": end},
    )
    result = []
    for r in rows:
        row = _as_dict(r)
        computed, billed = row["computed_usd"], row["billed_usd"]
        row["diff_pct"] = (
            round((computed - billed) / billed * 100, 2)
            if computed is not None and billed
            else None
        )
        result.append(row)
    return result


def period_bounds(session: Session, tenant_id: str) -> tuple[date, date] | None:
    """First and last day with usage for a tenant, or None without data."""
    first, last = session.execute(
        select(func.min(FctUsage.date_key), func.max(FctUsage.date_key)).where(
            FctUsage.tenant_id == tenant_id
        )
    ).one()
    if first is None:
        return None
    return _date_of(first), _date_of(last)


def unmapped_models(session: Session) -> list[dict]:
    rows = session.execute(
        select(DimModel.provider, DimModel.model_id).where(DimModel.is_mapped.is_(False))
    )
    return [_as_dict(r) for r in rows]


def _in_period(date_key_col, start: date | None, end: date | None) -> list:
    conditions = []
    if start is not None:
        conditions.append(date_key_col >= date_key_of(start))
    if end is not None:
        conditions.append(date_key_col <= date_key_of(end))
    return conditions


def _date_of(date_key: int) -> date:
    return date(date_key // 10_000, date_key // 100 % 100, date_key % 100)


def _as_dict(row: Row) -> dict:
    return dict(row._mapping)
