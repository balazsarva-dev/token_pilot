"""API sync: pull usage and billed cost from the providers' admin APIs into the ledger.

Same path as a CSV upload (import_parsed), with one difference: for the synced days the
API is complete and authoritative, so those days are replaced, not merged. That way an
earlier CSV of the same days cannot be counted twice (the CSV and API cost rows differ in
grain: the Anthropic cost export has keys, the cost report does not).

Window: up to yesterday (UTC; today's bucket is still filling). The first sync takes the
last 30 days; later syncs restart 3 days before the newest synced day, because providers
keep updating recent days. A sync covers its whole window even on days without usage, so
"synced through" comes from the import batch, not from the newest usage row.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tokenpilot.config import Settings
from tokenpilot.ledger.importer import import_parsed
from tokenpilot.ledger.models import DimDate, FctUsage, ImportBatch
from tokenpilot.sources import anthropic_api, openai_api
from tokenpilot.sources.http import JsonClient, SyncError, Transport, urllib_transport
from tokenpilot.sources.records import ParsedFile, RejectedRow

FIRST_SYNC_DAYS = 30
RESYNC_DAYS = 3
MAX_DAYS = 365

# provider -> (settings field holding the admin key, client factory, fetch)
PROVIDERS: dict[str, tuple[str, Callable[..., JsonClient], Callable[..., ParsedFile]]] = {
    "openai": ("openai_admin_key", openai_api.client, openai_api.fetch),
    "anthropic": ("anthropic_admin_key", anthropic_api.client, anthropic_api.fetch),
}


@dataclass
class SyncResult:
    provider: str
    start: date | None = None
    end: date | None = None
    ok: bool = True
    error: str | None = None
    unchanged: bool = False
    usage_rows: int = 0
    cost_rows: int = 0
    unpriced_rows: int = 0
    rejected: list[RejectedRow] = field(default_factory=list)
    unmapped_models: list[str] = field(default_factory=list)


def configured(settings: Settings) -> list[str]:
    return [p for p, (setting, _, _) in PROVIDERS.items() if getattr(settings, setting)]


def synced_through(session: Session, tenant_id: str, provider: str) -> date | None:
    """The newest day an API sync covered; without one, the newest day with usage (CSV)."""
    synced = session.scalar(
        select(func.max(ImportBatch.period_end)).where(
            ImportBatch.tenant_id == tenant_id, ImportBatch.provider == provider
        )
    )
    return synced or session.scalar(
        select(func.max(DimDate.date))
        .join(FctUsage, FctUsage.date_key == DimDate.date_key)
        .where(FctUsage.tenant_id == tenant_id, FctUsage.provider == provider)
    )


def sync_window(last_day: date | None, today: date, days: int | None = None) -> tuple[date, date]:
    end = today - timedelta(days=1)
    if days:
        start = end - timedelta(days=min(days, MAX_DAYS) - 1)
    elif last_day:
        start = min(last_day, end) - timedelta(days=RESYNC_DAYS - 1)
    else:
        start = end - timedelta(days=FIRST_SYNC_DAYS - 1)
    return max(start, end - timedelta(days=MAX_DAYS - 1)), end


def sync(
    session: Session,
    tenant_id: str,
    settings: Settings,
    providers: list[str] | None = None,
    days: int | None = None,
    today: date | None = None,
    transport: Transport = urllib_transport,
) -> list[SyncResult]:
    """Sync each configured provider. One provider failing does not stop the others."""
    available = configured(settings)
    wanted = providers or available
    missing = [p for p in wanted if p not in available]
    if not wanted or missing:
        names = " and ".join(missing) or "any provider"
        raise SyncError(
            f"No admin key configured for {names}. Add OPENAI_ADMIN_KEY / "
            "ANTHROPIC_ADMIN_KEY to backend/.env and restart."
        )
    today = today or datetime.now(UTC).date()

    results = []
    for provider in wanted:
        setting, make_client, fetch = PROVIDERS[provider]
        start, end = sync_window(synced_through(session, tenant_id, provider), today, days)
        result = SyncResult(provider, start, end)
        try:
            parsed = fetch(make_client(getattr(settings, setting), transport), start, end)
        except SyncError as exc:
            result.ok, result.error = False, str(exc)
            results.append(result)
            continue
        imported = import_parsed(
            session, tenant_id, parsed, _fingerprint(parsed, start, end), replace_days=(start, end)
        )
        result.unchanged = imported.already_imported
        result.usage_rows, result.cost_rows = imported.usage_rows, imported.cost_rows
        result.unpriced_rows = imported.unpriced_rows
        result.rejected, result.unmapped_models = parsed.rejected, imported.unmapped_models
        results.append(result)
    return results


def _fingerprint(parsed: ParsedFile, start: date, end: date) -> str:
    """Identical API data for the same window -> same id -> nothing to do."""
    payload = json.dumps(
        {"window": [start, end], "data": asdict(parsed)}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()
