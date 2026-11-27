"""Savings rules R1–R5. Each rule: (Ledger, AuditConfig) -> findings.

Estimates are candidates to test, never promises: every finding states its assumptions.
Unpriced usage is left out of every estimate (an unknown price is never treated as 0).
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from tokenpilot.money import ONE_MILLION, to_storage
from tokenpilot.pricing.cost import compute_cost
from tokenpilot.savings.model import AuditConfig, Finding, Ledger, UsageFact, input_tokens
from tokenpilot.sources.records import TokenCounts

MAD_TO_SIGMA = Decimal("1.4826")

GroupKey = tuple[int, int, int]  # api_key_key, scope_key, model_key


# ---------------------------------------------------------------- helpers


@dataclass(frozen=True)
class _Totals:
    cost: Decimal
    tokens: TokenCounts
    requests: int | None  # None if any fact lacks a request count

    @property
    def avg_input(self) -> int | None:
        return input_tokens(self.tokens) // self.requests if self.requests else None

    @property
    def avg_output(self) -> int | None:
        return self.tokens.output // self.requests if self.requests else None


def _totals(facts: list[UsageFact]) -> _Totals:
    requests = [f.requests for f in facts]
    return _Totals(
        cost=sum((f.cost for f in facts if f.cost is not None), Decimal(0)),
        tokens=sum((f.tokens for f in facts), TokenCounts()),
        requests=None if None in requests else sum(requests),
    )


def _priced_groups(ledger: Ledger, tiers: tuple[str, ...]) -> dict[GroupKey, list[UsageFact]]:
    groups: dict[GroupKey, list[UsageFact]] = defaultdict(list)
    for f in ledger.facts:
        if f.cost is not None and f.pricing_tier in tiers:
            groups[(f.api_key_key, f.scope_key, f.model_key)].append(f)
    return groups


def _usd(amount: Decimal) -> str:
    if 0 < amount < Decimal("0.01"):
        return f"${amount:.4f}"
    return f"${amount:,.2f}"


def _enough_requests(t: _Totals, cfg: AuditConfig) -> bool:
    return t.requests is not None and t.requests >= cfg.min_requests_for_medium


def _pct(part: Decimal) -> str:
    return f"{part * 100:.0f}%"


def _request_shape(t: _Totals) -> str:
    if not t.requests:
        return "an unknown number of requests (the data has no request counts)"
    noun = "request" if t.requests == 1 else "requests"
    return (
        f"{t.requests:,} {noun} averaging {t.avg_input:,} input and {t.avg_output:,} output tokens"
    )


# ---------------------------------------------------------------- R1 right-sizing


def right_sizing(ledger: Ledger, cfg: AuditConfig) -> list[Finding]:
    """Same tokens priced on the model's configured cheaper alternative."""
    findings = []
    for group, facts in _priced_groups(ledger, ("standard", "batch")).items():
        model = ledger.models[group[2]]
        alt_key = model.cheaper_alternative_key
        if alt_key is None:
            continue
        compared, alternative = [], Decimal(0)
        for f in facts:
            rates = ledger.rates_on(alt_key, f.day)
            alt_cost = compute_cost(f.tokens, rates, f.pricing_tier) if rates else None
            if alt_cost is not None:
                compared.append(f)
                alternative += alt_cost
        t = _totals(compared)
        saving = t.cost - alternative
        if saving <= 0:
            continue

        short = _enough_requests(t, cfg) and (
            t.avg_input <= cfg.short_input_tokens and t.avg_output <= cfg.short_output_tokens
        )
        alt_label = ledger.models[alt_key].label
        subject = ledger.subject(facts[0])
        monthly = ledger.monthly(saving)
        explanation = (
            f"{subject['key']} sent {_request_shape(t)} to {model.label}. "
            f"The same tokens on {alt_label} would cost {_usd(monthly)} less per month "
            f"({_pct(saving / t.cost)} less). "
            + ("Short requests like these often work well on a cheaper model. " if short else "")
            + "Compare answer quality on a sample before switching."
        )
        findings.append(
            Finding(
                rule="right_sizing",
                kind="saving",
                title=f"Test {alt_label} instead of {model.label}",
                explanation=explanation,
                confidence="medium" if short else "low",
                monthly_savings_usd=monthly,
                subject=subject,
                evidence={
                    "period_cost_usd": t.cost,
                    "alternative_cost_usd": alternative,
                    "alternative_model": alt_label,
                    "requests": t.requests,
                    "avg_input_tokens": t.avg_input,
                    "avg_output_tokens": t.avg_output,
                },
                assumptions=(
                    "The cheaper model uses the same number of input and output tokens.",
                    "Answer quality was not compared: this is a candidate to test.",
                ),
                group=group,
            )
        )
    return findings


# ---------------------------------------------------------------- R2 batch


def batch(ledger: Ledger, cfg: AuditConfig) -> list[Finding]:
    """A share of steady standard-price traffic could run on the discounted Batch API."""
    findings = []
    for group, facts in _priced_groups(ledger, ("standard", "batch")).items():
        standard = [f for f in facts if f.pricing_tier == "standard"]
        total = _totals(facts).cost
        standard_cost = _totals(standard).cost
        if standard_cost <= 0 or (total - standard_cost) / total >= cfg.batch_max_existing_share:
            continue
        active_days = len({f.day for f in standard})
        if active_days < cfg.batch_min_active_days:
            continue
        weekend_ratio = _weekend_ratio(standard)
        if weekend_ratio is not None and weekend_ratio < cfg.batch_min_weekend_ratio:
            continue  # spend drops on weekends: people are waiting for these answers

        saving, discount = Decimal(0), None
        for f in standard:
            rates = ledger.rates_on(f.model_key, f.day)
            if rates is not None and rates.batch_discount is not None:
                discount = rates.batch_discount
                saving += f.cost * cfg.batch_eligible_share * discount
        if saving <= 0:
            continue

        subject = ledger.subject(facts[0])
        monthly = ledger.monthly(saving)
        share = cfg.batch_eligible_share
        explanation = (
            f"{subject['key']} spent {_usd(ledger.monthly(standard_cost))} per month on "
            f"{subject['model']} at standard prices, on {active_days} of {ledger.days} days. "
            f"The Batch API is {_pct(discount)} cheaper for work that can wait up to 24 hours "
            "(reports, classification, evaluations, backfills). "
            f"If {_pct(share)} of this traffic can wait, that saves about {_usd(monthly)} "
            "per month."
        )
        findings.append(
            Finding(
                rule="batch",
                kind="saving",
                title=f"Move non-urgent {subject['model']} work to the Batch API",
                explanation=explanation,
                confidence="low",
                monthly_savings_usd=monthly,
                subject=subject,
                evidence={
                    "standard_cost_usd": standard_cost,
                    "active_days": active_days,
                    "weekend_ratio": weekend_ratio,
                    "batch_discount": discount,
                },
                assumptions=(
                    f"{_pct(share)} of the standard-price traffic can wait up to 24 hours.",
                    "Traffic that does not drop on weekends is treated as possibly automated; "
                    "daily data cannot show which requests are interactive.",
                ),
                group=group,
            )
        )
    return findings


# ---------------------------------------------------------------- R3 prompt caching


def caching(ledger: Ledger, cfg: AuditConfig) -> list[Finding]:
    """Long prompts with a low cache hit ratio: part of the input could be read from cache."""
    findings = []
    reads_per_write = cfg.cache_reads_per_write
    for group, facts in _priced_groups(ledger, ("standard",)).items():
        t = _totals(facts)
        prompt = t.tokens.input_uncached + t.tokens.input_cached
        if prompt == 0:
            continue
        hit_ratio = Decimal(t.tokens.input_cached) / prompt
        if hit_ratio >= cfg.cache_max_hit_ratio:
            continue
        if t.requests and t.avg_input < cfg.cache_min_prompt_tokens:
            continue  # prompts too short to be cached

        saving, cheaper_by = Decimal(0), None
        for f in facts:
            rates = ledger.rates_on(f.model_key, f.day)
            if rates is None or rates.input_uncached is None or rates.input_cached is None:
                continue
            cacheable = f.tokens.input_uncached * cfg.cache_cacheable_share
            reads = cacheable * reads_per_write / (reads_per_write + 1)
            writes = cacheable / (reads_per_write + 1)
            write_premium = (
                rates.cache_write_short - rates.input_uncached
                if rates.cache_write_short is not None
                else Decimal(0)
            )
            saving += (
                reads * (rates.input_uncached - rates.input_cached) - writes * write_premium
            ) / ONE_MILLION
            cheaper_by = (rates.input_uncached - rates.input_cached) / rates.input_uncached
        saving = to_storage(saving)
        if saving <= 0:
            continue

        subject = ledger.subject(facts[0])
        monthly = ledger.monthly(saving)
        size = f", while requests average {t.avg_input:,} input tokens" if t.requests else ""
        how = (
            "OpenAI caches automatically when the start of the prompt is identical: "
            "put static content first."
            if subject["provider"] == "openai"
            else "On Anthropic, mark the stable prefix with cache_control."
        )
        share_read = "None" if hit_ratio == 0 else f"Only {_pct(hit_ratio)}"
        explanation = (
            f"{share_read} of the input tokens {subject['key']} sent to "
            f"{subject['model']} were read from the prompt cache{size}. Cached tokens are "
            f"{_pct(cheaper_by)} cheaper. If {_pct(cfg.cache_cacheable_share)} of the uncached "
            f"input is a repeated prefix (system prompt, tool definitions, documents), caching "
            f"saves about {_usd(monthly)} per month. {how}"
        )
        findings.append(
            Finding(
                rule="caching",
                kind="saving",
                title=f"Cache the repeated prompt prefix for {subject['model']}",
                explanation=explanation,
                confidence="medium" if _enough_requests(t, cfg) else "low",
                monthly_savings_usd=monthly,
                subject=subject,
                evidence={
                    "cache_hit_ratio": to_storage(hit_ratio),
                    "uncached_input_tokens": t.tokens.input_uncached,
                    "avg_input_tokens": t.avg_input,
                },
                assumptions=(
                    f"{_pct(cfg.cache_cacheable_share)} of the uncached input is a repeated "
                    "prefix.",
                    f"Where cache writes cost extra, each prefix is written once per "
                    f"{reads_per_write} reads.",
                ),
                group=group,
            )
        )
    return findings


# ---------------------------------------------------------------- R4 anomalies


def anomalies(ledger: Ledger, cfg: AuditConfig) -> list[Finding]:
    """Days far above the usual daily spend of one key and model (median + k * robust spread)."""
    daily: dict[tuple[int, int], dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    sample: dict[tuple[int, int], UsageFact] = {}
    for f in ledger.facts:
        if f.cost is not None:
            daily[(f.api_key_key, f.model_key)][f.day] += f.cost
            sample[(f.api_key_key, f.model_key)] = f
    data_end = _data_end_by_provider(ledger)

    candidates = []
    for series_key, by_day in daily.items():
        fact = sample[series_key]
        days = _days(min(by_day), data_end[fact.provider])
        if len(days) < cfg.anomaly_min_days:
            continue
        values = [by_day.get(d, Decimal(0)) for d in days]
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        spread = max(mad * MAD_TO_SIGMA, median / 2, cfg.anomaly_min_usd)
        threshold = median + cfg.anomaly_k * spread
        for day, value in zip(days, values, strict=True):
            if value > threshold:
                candidates.append((value - median, day, value, median, fact))

    candidates.sort(key=lambda c: c[0], reverse=True)
    findings = []
    for excess, day, value, median, fact in candidates[: cfg.anomaly_max_findings]:
        subject = ledger.subject(fact)
        typical = (
            f"{value / median:.1f}× a typical day ({_usd(median)})"
            if median > 0
            else "while a typical day costs almost nothing"
        )
        findings.append(
            Finding(
                rule="anomaly",
                kind="investigate",
                title=f"Spend spike on {day.isoformat()}: {subject['key']}",
                explanation=(
                    f"On {day.isoformat()}, {subject['key']} spent {_usd(value)} on "
                    f"{subject['model']}, {typical}. Check for a retry loop, a runaway agent, "
                    "a leaked key or a launch nobody announced."
                ),
                confidence="high",
                monthly_savings_usd=None,
                subject=subject,
                evidence={
                    "date": day,
                    "cost_usd": value,
                    "typical_day_usd": median,
                    "excess_usd": excess,
                },
            )
        )
    return findings


# ---------------------------------------------------------------- R5 idle keys


def idle_keys(ledger: Ledger, cfg: AuditConfig) -> list[Finding]:
    """Active keys with no usage in the last weeks of the data: leak risk, revoke if unused."""
    last_used: dict[int, UsageFact] = {}
    spend: dict[int, Decimal] = defaultdict(Decimal)
    for f in ledger.facts:
        if f.api_key_key not in last_used or f.day > last_used[f.api_key_key].day:
            last_used[f.api_key_key] = f
        spend[f.api_key_key] += f.cost or 0
    data_end = _data_end_by_provider(ledger)

    findings = []
    for key, fact in last_used.items():
        info = ledger.keys[key]
        if not info.is_key or info.status not in (None, "active"):
            continue
        idle = (data_end[fact.provider] - fact.day).days
        if idle < cfg.idle_days:
            continue
        findings.append(
            Finding(
                rule="idle_key",
                kind="hygiene",
                title=f"Unused API key: {info.label}",
                explanation=(
                    f"{info.label} was last used on {fact.day.isoformat()}, {idle} days before "
                    f"the end of the data, after {_usd(spend[key])} of usage in this period. "
                    "A forgotten key is a leak risk: revoke it if nothing depends on it."
                ),
                confidence="medium",
                monthly_savings_usd=None,
                subject={"provider": fact.provider, "key": info.label},
                evidence={"last_used": fact.day, "idle_days": idle, "period_cost_usd": spend[key]},
            )
        )
    return findings


def _weekend_ratio(facts: list[UsageFact]) -> Decimal | None:
    """Median weekend-day spend / median weekday spend; None without both kinds of days.
    Medians, so one spike on a Sunday does not make interactive traffic look automated."""
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for f in facts:
        by_day[f.day] += f.cost
    weekend = [cost for day, cost in by_day.items() if day.isoweekday() >= 6]
    weekday = [cost for day, cost in by_day.items() if day.isoweekday() < 6]
    if not weekend or not weekday or statistics.median(weekday) == 0:
        return None
    return to_storage(statistics.median(weekend) / statistics.median(weekday))


def _data_end_by_provider(ledger: Ledger) -> dict[str, date]:
    """Exports of different providers can end on different days; compare within a provider."""
    ends: dict[str, date] = {}
    for f in ledger.facts:
        ends[f.provider] = max(ends.get(f.provider, f.day), f.day)
    return ends


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


RULES = (right_sizing, batch, caching, anomalies, idle_keys)
