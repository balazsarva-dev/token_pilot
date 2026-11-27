from datetime import date
from decimal import Decimal

import pytest

from tests import fixtures
from tokenpilot.sources.csv_files import parse_csv_bytes, parse_csv_text
from tokenpilot.sources.records import TokenCounts, UnsupportedFileError


def test_openai_usage_is_detected_and_empty_days_are_skipped():
    parsed = parse_csv_text(fixtures.OPENAI_USAGE)

    assert parsed.source_type == "openai_usage_csv"
    assert parsed.skipped_empty == 1
    [rec] = parsed.usage
    assert rec.day == date(2026, 9, 20)  # from the Unix start_time
    assert rec.model_name == "gpt-5-mini-2025-08-07"
    assert rec.api_key.natural_id == "key_A"
    assert rec.scope.natural_id == "proj_A"
    assert rec.tokens == TokenCounts(input_uncached=11512, output=12686)
    assert rec.requests == 13
    assert rec.pricing_tier == "standard"


def test_openai_usage_rejects_rows_whose_input_breakdown_does_not_add_up():
    bad = fixtures.OPENAI_USAGE.replace("11512.0,11512.0,12686.0", "11000.0,11512.0,12686.0", 1)
    parsed = parse_csv_text(bad)

    assert parsed.usage == []
    assert parsed.rejected[0].line == 2
    assert "input_tokens" in parsed.rejected[0].reason


def test_openai_cost_line_items_are_split_into_model_and_token_type():
    parsed = parse_csv_text(fixtures.OPENAI_COST)

    assert parsed.source_type == "openai_cost_csv"
    by_item = {(c.model_name, c.line_item, c.token_type): c.amount_usd for c in parsed.costs}
    assert by_item == {
        ("gpt-5-mini-2025-08-07", "token", "input_cached"): Decimal(0),
        ("gpt-5-mini-2025-08-07", "token", "input_uncached"): Decimal("0.002878"),
        ("gpt-5-mini-2025-08-07", "token", "output"): Decimal("0.025372"),
        (None, "file search tool calls", None): Decimal("0.005"),
    }
    assert parsed.costs[0].scope.name == "Project A"


def test_ungrouped_openai_cost_export_is_refused_with_instructions():
    with pytest.raises(UnsupportedFileError, match="Group by: Line item"):
        parse_csv_text(fixtures.OPENAI_COST_UNGROUPED)


def test_anthropic_usage_maps_counters_and_tiers():
    parsed = parse_csv_text(fixtures.ANTHROPIC_USAGE)

    assert parsed.source_type == "anthropic_usage_csv"
    first, second, fast = parsed.usage
    assert first.tokens == TokenCounts(input_uncached=2498, output=1032)
    assert first.api_key.natural_id == "key-one"  # usage export has names only
    assert first.requests is None
    assert second.tokens.input_uncached == 2  # summed with `first` at import time
    assert fast.pricing_tier == "fast"


def test_anthropic_cost_keeps_money_rows_and_remembers_keys_from_zero_rows():
    parsed = parse_csv_text(fixtures.ANTHROPIC_COST)

    [cost] = parsed.costs
    assert (cost.model_name, cost.token_type, cost.amount_usd) == (
        "Claude Sonnet 5",
        "output",
        Decimal("0.01"),
    )
    assert cost.api_key.key_id == "apikey_01ONE"
    assert len(parsed.seen_keys) == 2  # the two 0.00 rows


def test_unknown_format_lists_expected_columns():
    with pytest.raises(UnsupportedFileError, match="Unrecognized export format"):
        parse_csv_text("a,b,c\n1,2,3\n")


def test_byte_order_mark_from_spreadsheets_is_ignored():
    parsed = parse_csv_bytes(("﻿" + fixtures.ANTHROPIC_USAGE).encode())
    assert parsed.source_type == "anthropic_usage_csv"
