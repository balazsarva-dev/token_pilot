from decimal import Decimal

import pytest

from tokenpilot.money import NumberFormatError, parse_count, parse_decimal, to_storage
from tokenpilot.pricing.cost import Rates, compute_cost
from tokenpilot.sources.records import TokenCounts

GPT5_MINI = Rates(
    input_uncached=Decimal("0.25"),
    input_cached=Decimal("0.025"),
    output=Decimal("2.00"),
    batch_discount=Decimal("0.5"),
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.002878000000000000000000", Decimal("0.002878")),
        ("0E-6176", Decimal(0)),
        ("  12.5 ", Decimal("12.5")),
        ("", None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


def test_parse_decimal_rejects_garbage_and_infinity():
    for raw in ("abc", "Infinity", "NaN"):
        with pytest.raises(NumberFormatError):
            parse_decimal(raw)


def test_parse_count_accepts_float_notation_of_whole_numbers():
    assert parse_count("11512.0") == 11512


@pytest.mark.parametrize("raw", ["1.5", "-3"])
def test_parse_count_rejects_fractions_and_negatives(raw):
    with pytest.raises(NumberFormatError):
        parse_count(raw)


def test_cost_matches_openai_bill_to_the_last_digit():
    # Real sample: 11,512 input + 12,686 output tokens of gpt-5-mini billed $0.028250.
    cost = compute_cost(TokenCounts(input_uncached=11512, output=12686), GPT5_MINI, "standard")
    assert cost == Decimal("0.0282500000")


def test_batch_tier_applies_the_discount():
    cost = compute_cost(TokenCounts(input_uncached=1_000_000), GPT5_MINI, "batch")
    assert cost == Decimal("0.125")


def test_missing_rate_for_used_token_type_means_unpriced_not_free():
    tokens = TokenCounts(input_uncached=10, cache_write_short=5)  # GPT5_MINI has no write price
    assert compute_cost(tokens, GPT5_MINI, "standard") is None


def test_unused_token_type_without_rate_does_not_block_pricing():
    assert compute_cost(TokenCounts(output=1_000_000), GPT5_MINI, "standard") == Decimal("2")


@pytest.mark.parametrize("tier", ["fast", "us_geo", "priority", "audio"])
def test_tiers_not_priced_in_v1_return_none(tier):
    assert compute_cost(TokenCounts(output=1), GPT5_MINI, tier) is None


def test_storage_rounding_is_half_up_once():
    assert to_storage(Decimal("0.00000000005")) == Decimal("0.0000000001")
