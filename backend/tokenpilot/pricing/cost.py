"""The cost formula — a pure function, so it can be tested to the last digit."""

from dataclasses import dataclass, fields
from decimal import Decimal

from tokenpilot.money import ONE_MILLION, to_storage
from tokenpilot.sources.records import TokenCounts

PRICED_TIERS = ("standard", "batch")


@dataclass(frozen=True)
class Rates:
    """USD per 1M tokens per token type. None = not published."""

    input_uncached: Decimal | None = None
    input_cached: Decimal | None = None
    cache_write_short: Decimal | None = None
    cache_write_long: Decimal | None = None
    output: Decimal | None = None
    batch_discount: Decimal | None = None


def compute_cost(tokens: TokenCounts, rates: Rates, pricing_tier: str) -> Decimal | None:
    """Cost in USD, or None when it cannot be priced honestly.

    None (never 0) when: the tier is not priced in v1, a token type with usage has no
    published rate, or a batch row's model has no batch discount.
    """
    if pricing_tier not in PRICED_TIERS:
        return None

    total = Decimal(0)
    for f in fields(TokenCounts):
        count = getattr(tokens, f.name)
        if count == 0:
            continue
        rate = getattr(rates, f.name)
        if rate is None:
            return None
        total += count * rate

    if pricing_tier == "batch":
        if rates.batch_discount is None:
            return None
        total *= 1 - rates.batch_discount

    return to_storage(total / ONE_MILLION)
