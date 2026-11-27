"""Anthropic Console exports: Usage page (tokens) and Cost page (USD).

Observed quirks (docs/export-formats.md): "--" means not applicable, the usage export
identifies keys and workspaces by *name only*, the cost export rounds each row to cents,
and model names differ (API id in usage, display name in cost).
"""

from collections.abc import Iterable
from datetime import date

from tokenpilot.money import NumberFormatError, parse_count, parse_decimal
from tokenpilot.sources.records import (
    CostRecord,
    KeyRef,
    ParsedFile,
    RejectedRow,
    ScopeRef,
    TokenCounts,
    UsageRecord,
)

PROVIDER = "anthropic"
NOT_APPLICABLE = "--"

COST_TOKEN_TYPES = {
    "input_no_cache": "input_uncached",
    "input_cache_read": "input_cached",
    "input_cache_write_5m": "cache_write_short",
    "input_cache_write_1h": "cache_write_long",
    "output": "output",
}


class RowError(ValueError):
    pass


def _value(row: dict[str, str], column: str) -> str:
    value = row.get(column, "").strip()
    return "" if value == NOT_APPLICABLE else value


def _day(row: dict[str, str]) -> date:
    try:
        return date.fromisoformat(_value(row, "usage_date_utc"))
    except ValueError as exc:
        raise RowError(f"bad usage_date_utc: {row.get('usage_date_utc')!r}") from exc


def pricing_tier(speed: str, inference_geo: str, usage_type: str) -> str:
    """standard / batch are priced in v1; fast mode and US-only inference are kept unpriced."""
    if speed == "fast":
        return "fast"
    if inference_geo == "us":
        return "us_geo"  # 1.1x multiplier on all token prices
    return "standard" if usage_type in ("", "standard", "message") else usage_type


def _pricing_tier(row: dict[str, str], usage_type_column: str) -> str:
    return pricing_tier(
        _value(row, "speed"), _value(row, "inference_geo"), _value(row, usage_type_column)
    )


def _key(row: dict[str, str]) -> KeyRef:
    name = _value(row, "api_key")
    return KeyRef(
        natural_id=name,
        key_id=_value(row, "api_key_id") or None,
        name=name or None,
        status=_value(row, "api_key_status") or None,
    )


def _scope(row: dict[str, str]) -> ScopeRef:
    workspace = _value(row, "workspace")
    return ScopeRef(kind="workspace", natural_id=workspace, name=workspace or None)


def _count(row: dict[str, str], column: str) -> int:
    try:
        return parse_count(_value(row, column)) or 0
    except NumberFormatError as exc:
        raise RowError(f"{column}: {exc}") from exc


def parse_usage(reader: Iterable[dict[str, str]]) -> ParsedFile:
    parsed = ParsedFile(source_type="anthropic_usage_csv", provider=PROVIDER)
    for line, row in enumerate(reader, start=2):
        model = _value(row, "model_version")
        if not model:
            parsed.skipped_empty += 1
            continue
        try:
            parsed.usage.append(
                UsageRecord(
                    provider=PROVIDER,
                    day=_day(row),
                    model_name=model,
                    api_key=_key(row),
                    scope=_scope(row),
                    pricing_tier=_pricing_tier(row, "usage_type"),
                    tokens=TokenCounts(
                        input_uncached=_count(row, "usage_input_tokens_no_cache"),
                        input_cached=_count(row, "usage_input_tokens_cache_read"),
                        cache_write_short=_count(row, "usage_input_tokens_cache_write_5m"),
                        cache_write_long=_count(row, "usage_input_tokens_cache_write_1h"),
                        output=_count(row, "usage_output_tokens"),
                    ),
                    requests=None,  # not in the export
                )
            )
        except RowError as exc:
            parsed.rejected.append(RejectedRow(line, str(exc)))
    return parsed


def parse_cost(reader: Iterable[dict[str, str]]) -> ParsedFile:
    parsed = ParsedFile(source_type="anthropic_cost_csv", provider=PROVIDER)
    for line, row in enumerate(reader, start=2):
        try:
            day = _day(row)
            amount = parse_decimal(_value(row, "cost_usd"))
            if amount is None:
                raise RowError("missing cost_usd")
            key = _key(row)
            if amount == 0:
                # No money, but the row still tells us the key exists (id, status).
                parsed.seen_keys.append((day, key))
                continue

            cost_type = _value(row, "cost_type")
            if cost_type == "token":
                raw_type = _value(row, "token_type")
                if raw_type not in COST_TOKEN_TYPES:
                    raise RowError(f"unknown token_type {raw_type!r}")
                line_item, token_type = "token", COST_TOKEN_TYPES[raw_type]
                model = _value(row, "model") or None
            else:
                line_item, token_type, model = cost_type or "unknown", None, None

            parsed.costs.append(
                CostRecord(
                    provider=PROVIDER,
                    day=day,
                    scope=_scope(row),
                    api_key=key,
                    model_name=model,
                    line_item=line_item,
                    token_type=token_type,
                    amount_usd=amount,
                )
            )
        except (RowError, NumberFormatError) as exc:
            parsed.rejected.append(RejectedRow(line, str(exc)))
    return parsed
