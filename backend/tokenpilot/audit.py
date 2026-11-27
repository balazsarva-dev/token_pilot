"""The AI bill audit: load a tenant's ledger, run the savings rules, assemble one report.

The report is computed on request and not stored (it is derived data and cheap to recompute).
It is a plain dict of Decimals, dates and strings; the web layer turns it into JSON.
"""

from dataclasses import asdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tokenpilot.ledger import reports
from tokenpilot.ledger.dimensions import date_key_of
from tokenpilot.ledger.models import DimApiKey, DimDate, DimModel, DimScope, FctUsage
from tokenpilot.ledger.prices import PriceBook, rates_of
from tokenpilot.pricing.cost import Rates
from tokenpilot.savings.engine import run_audit
from tokenpilot.savings.model import AuditConfig, KeyInfo, Ledger, ModelInfo, UsageFact
from tokenpilot.sources.records import TokenCounts

TOP_N = 10
PROVIDER_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic"}


def load_ledger(session: Session, tenant_id: str, start: date, end: date) -> Ledger:
    rows = session.execute(
        select(FctUsage, DimDate.date)
        .join(DimDate, DimDate.date_key == FctUsage.date_key)
        .where(
            FctUsage.tenant_id == tenant_id,
            FctUsage.date_key >= date_key_of(start),
            FctUsage.date_key <= date_key_of(end),
        )
    )
    facts = [
        UsageFact(
            day=day,
            provider=u.provider,
            model_key=u.model_key,
            api_key_key=u.api_key_key,
            scope_key=u.scope_key,
            pricing_tier=u.pricing_tier,
            tokens=TokenCounts(
                u.input_uncached, u.input_cached, u.cache_write_short, u.cache_write_long, u.output
            ),
            requests=u.requests,
            cost=u.computed_cost,
        )
        for u, day in rows
    ]
    models = {
        m.model_key: ModelInfo(m.display_name or m.model_id, m.cheaper_alternative_key)
        for m in session.scalars(select(DimModel))
    }
    keys = {
        k.api_key_key: KeyInfo(
            label=k.name or k.natural_id or "(no API key)",
            status=k.status,
            is_key=k.natural_id != "",
        )
        for k in session.scalars(select(DimApiKey).where(DimApiKey.tenant_id == tenant_id))
    }
    scopes = {
        s.scope_key: s.name or s.natural_id or "(default)"
        for s in session.scalars(select(DimScope).where(DimScope.tenant_id == tenant_id))
    }
    prices = PriceBook(session)

    def rates_on(model_key: int, day: date) -> Rates | None:
        price = prices.find(model_key, day)
        return rates_of(price) if price else None

    return Ledger(start, end, facts, models, keys, scopes, rates_on)


def build_audit(
    session: Session,
    tenant_id: str,
    start: date | None = None,
    end: date | None = None,
    cfg: AuditConfig | None = None,
) -> dict | None:
    """The audit report for a period (default: all data). None if the tenant has no usage."""
    cfg = cfg or AuditConfig()
    bounds = reports.period_bounds(session, tenant_id)
    if bounds is None:
        return None
    start, end = start or bounds[0], end or bounds[1]

    ledger = load_ledger(session, tenant_id, start, end)
    result = run_audit(ledger, cfg)
    total = sum((f.cost for f in ledger.facts if f.cost is not None), Decimal(0))
    by_provider: dict[str, Decimal] = {}
    for f in ledger.facts:
        by_provider[f.provider] = by_provider.get(f.provider, Decimal(0)) + (f.cost or 0)

    findings = []
    for f in result.findings:
        finding = asdict(f)
        del finding["group"]  # internal
        finding["assumptions"] = list(f.assumptions)
        findings.append(finding)

    return {
        "tenant": tenant_id,
        "period": {"start": start, "end": end, "days": ledger.days},
        "spend": {
            "total_usd": total,
            "monthly_run_rate_usd": ledger.monthly(total),
            "monthly_after_likely_usd": ledger.monthly(total) - result.likely_monthly_usd,
            "by_provider": [{"provider": p, "usd": usd} for p, usd in sorted(by_provider.items())],
            **{
                f"by_{g}": reports.spend_by(session, tenant_id, g, start, end)[:TOP_N]
                for g in ("model", "key", "scope")
            },
        },
        "savings": {
            "likely_monthly_usd": result.likely_monthly_usd,
            "potential_monthly_usd": result.potential_monthly_usd,
            "findings": sum(f.kind == "saving" for f in result.findings),
            "hidden_small": result.hidden_small,
        },
        "findings": findings,
        "data_quality": {
            "reconciliation": reports.reconciliation_summary(session, tenant_id, start, end),
            "unmapped_models": reports.unmapped_models(session),
            "notes": _notes(ledger),
        },
        "config": asdict(cfg),
    }


def _notes(ledger: Ledger) -> list[str]:
    notes = []
    if ledger.days < 14:
        notes.append(
            f"Only {ledger.days} days of data: monthly projections are rough. "
            "Upload at least two weeks for a reliable audit."
        )
    unpriced = sum(f.cost is None for f in ledger.facts)
    if unpriced:
        notes.append(
            f"{unpriced} usage rows could not be priced (fast mode, US-only inference, audio "
            "or models missing from the price list). They are left out of all estimates."
        )
    without_requests = sorted({f.provider for f in ledger.facts if f.requests is None})
    for provider in without_requests:
        notes.append(
            f"The {PROVIDER_NAMES.get(provider, provider)} data has no request counts "
            "(neither its exports nor its usage API include them), so checks based on request "
            "size are less certain."
        )
    return notes
