"""Price lookup (SCD2) and re-pricing of stored usage."""

from collections import defaultdict
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tokenpilot.ledger.models import DimDate, DimModelPrice, FctUsage
from tokenpilot.pricing.cost import Rates, compute_cost
from tokenpilot.sources.records import TokenCounts


class PriceBook:
    """All price versions in memory; finds the one valid on a given day."""

    def __init__(self, session: Session):
        self._by_model: dict[int, list[DimModelPrice]] = defaultdict(list)
        for price in session.scalars(select(DimModelPrice).order_by(DimModelPrice.valid_from)):
            self._by_model[price.model_key].append(price)

    def find(self, model_key: int, day: date) -> DimModelPrice | None:
        for price in self._by_model.get(model_key, []):
            if price.valid_from <= day and (price.valid_to is None or day < price.valid_to):
                return price
        return None


def rates_of(price: DimModelPrice) -> Rates:
    return Rates(
        input_uncached=price.input_uncached,
        input_cached=price.input_cached,
        cache_write_short=price.cache_write_short,
        cache_write_long=price.cache_write_long,
        output=price.output,
        batch_discount=price.batch_discount,
    )


def price_usage(prices: PriceBook, model_key: int, day: date, tokens: TokenCounts, tier: str):
    """(computed_cost, price_key) for one usage row; (None, None) when unpriced."""
    price = prices.find(model_key, day)
    if price is None:
        return None, None
    cost = compute_cost(tokens, rates_of(price), tier)
    return (cost, price.price_key) if cost is not None else (None, None)


def reprice(session: Session, tenant_id: str | None = None) -> int:
    """Recompute cost for stored usage, e.g. after a price change. Returns rows changed."""
    prices = PriceBook(session)
    query = select(FctUsage, DimDate.date).join(DimDate, DimDate.date_key == FctUsage.date_key)
    if tenant_id is not None:
        query = query.where(FctUsage.tenant_id == tenant_id)

    changed = 0
    for row, day in session.execute(query):
        tokens = TokenCounts(
            row.input_uncached,
            row.input_cached,
            row.cache_write_short,
            row.cache_write_long,
            row.output,
        )
        cost, price_key = price_usage(prices, row.model_key, day, tokens, row.pricing_tier)
        if cost != row.computed_cost or price_key != row.price_key:
            session.execute(
                update(FctUsage)
                .where(FctUsage.id == row.id)
                .values(computed_cost=cost, price_key=price_key)
            )
            changed += 1
    return changed
