"""Plain data the savings rules work on. No database and no Flask: rules are pure functions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tokenpilot.money import to_storage
from tokenpilot.pricing.cost import Rates
from tokenpilot.sources.records import TokenCounts


@dataclass(frozen=True)
class UsageFact:
    """One fct_usage row with its dimension keys."""

    day: date
    provider: str
    model_key: int
    api_key_key: int
    scope_key: int
    pricing_tier: str
    tokens: TokenCounts
    requests: int | None  # None = not in the source (Anthropic Console CSV)
    cost: Decimal | None  # None = unpriced


@dataclass(frozen=True)
class ModelInfo:
    label: str
    cheaper_alternative_key: int | None = None


@dataclass(frozen=True)
class KeyInfo:
    label: str
    status: str | None = None
    is_key: bool = True  # False = usage without an API key (e.g. Console playground)


@dataclass
class Ledger:
    """Everything one audit looks at: a tenant's usage in a period, plus the catalog."""

    start: date
    end: date
    facts: list[UsageFact]
    models: dict[int, ModelInfo]
    keys: dict[int, KeyInfo]
    scopes: dict[int, str]  # scope_key -> label
    rates_on: Callable[[int, date], Rates | None]  # (model_key, day) -> rates valid that day

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def monthly(self, amount: Decimal) -> Decimal:
        """Project an amount observed over the period to 30 days."""
        return to_storage(amount * 30 / self.days)

    def subject(self, fact: UsageFact) -> dict[str, str]:
        return {
            "provider": fact.provider,
            "model": self.models[fact.model_key].label,
            "key": self.keys[fact.api_key_key].label,
            "scope": self.scopes[fact.scope_key],
        }


@dataclass(frozen=True)
class AuditConfig:
    """Thresholds and assumptions. Echoed in every report, so a reader can see what was assumed."""

    min_monthly_savings: Decimal = Decimal("1")  # smaller savings are counted but not shown
    min_requests_for_medium: int = 100  # fewer requests are too little evidence: low confidence
    # R1 right-sizing: requests this short are typical for a cheaper model (medium confidence).
    short_input_tokens: int = 4000
    short_output_tokens: int = 500
    # R2 batch
    batch_eligible_share: Decimal = Decimal("0.25")
    batch_min_active_days: int = 5
    batch_max_existing_share: Decimal = Decimal("0.5")  # skip if already mostly on batch
    # Weekend daily spend below this share of weekday spend = traffic follows people.
    batch_min_weekend_ratio: Decimal = Decimal("0.7")
    # R3 prompt caching
    cache_max_hit_ratio: Decimal = Decimal("0.2")
    cache_min_prompt_tokens: int = 1024  # providers do not cache shorter prompts
    cache_cacheable_share: Decimal = Decimal("0.5")
    cache_reads_per_write: int = 5
    # R4 anomalies: day > median + k * spread, spread = max(1.4826 * MAD, median / 2, min_usd)
    anomaly_k: Decimal = Decimal("4")
    anomaly_min_days: int = 7
    anomaly_min_usd: Decimal = Decimal("1")
    anomaly_max_findings: int = 5
    # R5 idle keys
    idle_days: int = 14


@dataclass(frozen=True)
class Finding:
    rule: str  # right_sizing / batch / caching / anomaly / idle_key
    kind: str  # saving / investigate / hygiene
    title: str
    explanation: str
    confidence: str  # low / medium / high
    monthly_savings_usd: Decimal | None  # None for findings that are not savings
    subject: dict[str, str]
    evidence: dict[str, object]
    assumptions: tuple[str, ...] = ()
    # Findings with the same group act on the same spend, so their savings do not add up.
    group: tuple | None = None


def input_tokens(tokens: TokenCounts) -> int:
    """All prompt tokens: uncached, read from cache and written to cache."""
    return (
        tokens.input_uncached
        + tokens.input_cached
        + tokens.cache_write_short
        + tokens.cache_write_long
    )
