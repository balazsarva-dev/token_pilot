"""Import (CSV, demo, API sync): parse -> resolve dimensions -> aggregate to fact grain -> upsert.

Idempotent at two levels:
- the same file (SHA-256) is imported once per tenant;
- facts are upserted on their grain, so overlapping exports converge instead of doubling.
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tokenpilot.ledger.dimensions import Dimensions, date_key_of
from tokenpilot.ledger.models import (
    DimApiKey,
    DimScope,
    FctBilledCost,
    FctUsage,
    ImportBatch,
)
from tokenpilot.ledger.prices import PriceBook, price_usage
from tokenpilot.sources.csv_files import parse_csv_bytes
from tokenpilot.sources.records import ParsedFile, RejectedRow, TokenCounts

USAGE_COUNTERS = (
    "input_uncached",
    "input_cached",
    "cache_write_short",
    "cache_write_long",
    "output",
)


@dataclass
class ImportResult:
    batch_id: int
    source_type: str
    provider: str
    already_imported: bool = False
    usage_rows: int = 0
    cost_rows: int = 0
    unpriced_rows: int = 0
    skipped_empty: int = 0
    rejected: list[RejectedRow] = field(default_factory=list)
    unmapped_models: list[str] = field(default_factory=list)


def import_csv(session: Session, tenant_id: str, data: bytes) -> ImportResult:
    """Import one export file. Commits on success; raises UnsupportedFileError for bad files."""
    sha256 = hashlib.sha256(data).hexdigest()
    existing = _existing_batch(session, tenant_id, sha256)
    if existing:
        return existing
    return import_parsed(session, tenant_id, parse_csv_bytes(data), sha256)


def import_parsed(
    session: Session,
    tenant_id: str,
    parsed: ParsedFile,
    sha256: str,
    replace_days: tuple[date, date] | None = None,
) -> ImportResult:
    """Load already-parsed records (CSV, demo data, API sync). Commits on success.

    sha256 identifies the source for lineage; the same source is imported once per tenant.
    replace_days: the source is complete and authoritative for these days (API sync), so
    the provider's existing facts for them are removed first, in the same transaction.
    """
    existing = _existing_batch(session, tenant_id, sha256)
    if existing:
        return existing
    if replace_days:
        _delete_days(session, tenant_id, parsed.provider, *replace_days)

    batch = ImportBatch(
        tenant_id=tenant_id,
        provider=parsed.provider,
        source_type=parsed.source_type,
        file_sha256=sha256,
        rows_ok=0,
        rows_rejected=len(parsed.rejected),
        period_start=replace_days[0] if replace_days else None,
        period_end=replace_days[1] if replace_days else None,
    )
    session.add(batch)
    session.flush()

    dims = Dimensions(session, tenant_id)
    result = ImportResult(
        batch.id,
        parsed.source_type,
        parsed.provider,
        skipped_empty=parsed.skipped_empty,
        rejected=parsed.rejected,
    )
    result.usage_rows, result.unpriced_rows = _load_usage(
        session, tenant_id, batch.id, parsed, dims
    )
    result.cost_rows = _load_costs(session, tenant_id, batch.id, parsed, dims)
    for day, key in parsed.seen_keys:
        dims.api_key_key(parsed.provider, key, day)
    for key in parsed.known_keys:
        dims.enrich_key(parsed.provider, key)

    batch.rows_ok = result.usage_rows + result.cost_rows
    result.unmapped_models = sorted(name for _, name in dims.unmapped_models)
    session.commit()
    return result


def delete_tenant_data(session: Session, tenant_id: str) -> None:
    """Remove everything imported for a tenant. Shared dimensions (dates, models, prices) stay."""
    for table in (FctUsage, FctBilledCost, ImportBatch, DimApiKey, DimScope):
        session.execute(delete(table).where(table.tenant_id == tenant_id))
    session.commit()


def _delete_days(session: Session, tenant_id: str, provider: str, start: date, end: date) -> None:
    for table in (FctUsage, FctBilledCost):
        session.execute(
            delete(table).where(
                table.tenant_id == tenant_id,
                table.provider == provider,
                table.date_key.between(date_key_of(start), date_key_of(end)),
            )
        )


def _existing_batch(session: Session, tenant_id: str, sha256: str) -> ImportResult | None:
    batch = session.scalar(
        select(ImportBatch).where(
            ImportBatch.tenant_id == tenant_id, ImportBatch.file_sha256 == sha256
        )
    )
    if batch is None:
        return None
    return ImportResult(batch.id, batch.source_type, batch.provider, already_imported=True)


def _load_usage(
    session: Session, tenant_id: str, batch_id: int, parsed: ParsedFile, dims: Dimensions
) -> tuple[int, int]:
    # Several export rows can map to one grain (e.g. Anthropic splits by context window).
    tokens_by_grain: dict[tuple, TokenCounts] = defaultdict(TokenCounts)
    requests_by_grain: dict[tuple, int | None] = defaultdict(int)
    day_of: dict[tuple, object] = {}
    for rec in parsed.usage:
        grain = (
            dims.date_key(rec.day),
            dims.model_key(rec.provider, rec.model_name),
            dims.api_key_key(rec.provider, rec.api_key, rec.day),
            dims.scope_key(rec.provider, rec.scope),
            rec.pricing_tier,
        )
        tokens_by_grain[grain] += rec.tokens
        previous = requests_by_grain[grain]
        requests_by_grain[grain] = (
            None if previous is None or rec.requests is None else previous + rec.requests
        )
        day_of[grain] = rec.day

    prices = PriceBook(session)
    unpriced = 0
    for grain, tokens in tokens_by_grain.items():
        date_key, model_key, api_key_key, scope_key, tier = grain
        cost, price_key = price_usage(prices, model_key, day_of[grain], tokens, tier)
        unpriced += cost is None
        values = {
            "tenant_id": tenant_id,
            "provider": parsed.provider,
            "date_key": date_key,
            "model_key": model_key,
            "api_key_key": api_key_key,
            "scope_key": scope_key,
            "pricing_tier": tier,
            **{name: getattr(tokens, name) for name in USAGE_COUNTERS},
            "requests": requests_by_grain[grain],
            "computed_cost": cost,
            "price_key": price_key,
            "import_batch_id": batch_id,
        }
        stmt = insert(FctUsage).values(**values)
        updated = {k: stmt.excluded[k] for k in values if k not in _USAGE_GRAIN}
        session.execute(
            stmt.on_conflict_do_update(constraint="uq_fct_usage_tenant_id", set_=updated)
        )
    return len(tokens_by_grain), unpriced


_USAGE_GRAIN = {"tenant_id", "date_key", "model_key", "api_key_key", "scope_key", "pricing_tier"}


def _load_costs(
    session: Session, tenant_id: str, batch_id: int, parsed: ParsedFile, dims: Dimensions
) -> int:
    amount_by_grain: dict[tuple, Decimal] = defaultdict(Decimal)
    for rec in parsed.costs:
        grain = (
            dims.date_key(rec.day),
            dims.scope_key(rec.provider, rec.scope),
            dims.api_key_key(rec.provider, rec.api_key, rec.day) if rec.api_key else None,
            dims.model_key(rec.provider, rec.model_name) if rec.model_name else None,
            rec.line_item,
            rec.token_type,
        )
        amount_by_grain[grain] += rec.amount_usd

    for grain, amount in amount_by_grain.items():
        date_key, scope_key, api_key_key, model_key, line_item, token_type = grain
        stmt = insert(FctBilledCost).values(
            tenant_id=tenant_id,
            provider=parsed.provider,
            date_key=date_key,
            scope_key=scope_key,
            api_key_key=api_key_key,
            model_key=model_key,
            line_item=line_item,
            token_type=token_type,
            amount_usd=amount,
            import_batch_id=batch_id,
        )
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_fct_billed_cost_tenant_id",
                set_={
                    "amount_usd": stmt.excluded.amount_usd,
                    "import_batch_id": stmt.excluded.import_batch_id,
                },
            )
        )
    return len(amount_by_grain)
