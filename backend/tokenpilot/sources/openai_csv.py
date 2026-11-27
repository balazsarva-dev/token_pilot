"""OpenAI Usage-page exports: "Activity" (completions usage) and "Cost data".

Observed quirks (docs/export-formats.md): days without spend appear as empty rows,
counts are written as "11512.0", zero amounts as "0E-6176", and the ISO time columns
are inconsistent (with and without "+00:00"), so the Unix start_time is authoritative.
The admin API returns the same fields, so openai_api.py feeds its results through here too.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime

from tokenpilot.money import NumberFormatError, parse_count, parse_decimal
from tokenpilot.sources.records import (
    CostRecord,
    KeyRef,
    ParsedFile,
    RejectedRow,
    ScopeRef,
    TokenCounts,
    UnsupportedFileError,
    UsageRecord,
)

PROVIDER = "openai"
ONE_DAY_SECONDS = 86_400

# Cost line items look like "gpt-5-mini-2025-08-07, cached input".
LINE_ITEM_TOKEN_TYPES = {
    "input": "input_uncached",
    "cached input": "input_cached",
    "output": "output",
}
NON_TEXT_COUNTERS = ("input_audio_tokens", "input_cached_audio_tokens", "output_audio_tokens")


class RowError(ValueError):
    pass


def _day(row: dict[str, str]) -> date:
    try:
        start = int(parse_count(row["start_time"]) or 0)
        end = int(parse_count(row["end_time"]) or 0)
    except NumberFormatError as exc:
        raise RowError(f"bad start/end time: {exc}") from exc
    if end - start != ONE_DAY_SECONDS:
        raise RowError("only 1-day buckets are supported; export with a 1 day interval")
    return datetime.fromtimestamp(start, tz=UTC).date()


def _count(row: dict[str, str], column: str) -> int:
    try:
        return parse_count(row.get(column, "")) or 0
    except NumberFormatError as exc:
        raise RowError(f"{column}: {exc}") from exc


def _pricing_tier(row: dict[str, str]) -> str:
    if row.get("batch", "").strip().lower() == "true":
        return "batch"
    tier = row.get("service_tier", "").strip().lower()
    return "standard" if tier in ("", "default", "auto") else tier


def _usage_row(row: dict[str, str]) -> UsageRecord:
    total_input = _count(row, "input_tokens")
    cached = _count(row, "input_cached_tokens")
    uncached = _count(row, "input_uncached_tokens") if row.get("input_uncached_tokens") else None
    if uncached is None:
        uncached = total_input - cached
    if uncached + cached != total_input:
        raise RowError(
            f"input_uncached_tokens ({uncached}) + input_cached_tokens ({cached}) "
            f"!= input_tokens ({total_input})"
        )

    write_short = _count(row, "input_cache_write_30m_tokens")
    write_long = _count(row, "input_cache_write_12h_tokens")
    write_total = _count(row, "input_cache_write_tokens")
    if write_total != write_short + write_long:
        raise RowError(
            f"input_cache_write_tokens ({write_total}) does not match its 30m + 12h breakdown"
        )

    tier = _pricing_tier(row)
    if any(_count(row, col) for col in NON_TEXT_COUNTERS):
        tier = "audio"  # audio tokens have their own prices; kept but unpriced in v1

    return UsageRecord(
        provider=PROVIDER,
        day=_day(row),
        model_name=row["model"].strip(),
        api_key=KeyRef(
            natural_id=row.get("api_key_id", "").strip(), key_id=_opt(row, "api_key_id")
        ),
        scope=ScopeRef(kind="project", natural_id=row.get("project_id", "").strip()),
        pricing_tier=tier,
        tokens=TokenCounts(
            input_uncached=uncached,
            input_cached=cached,
            cache_write_short=write_short,
            cache_write_long=write_long,
            output=_count(row, "output_tokens"),
        ),
        requests=_count(row, "num_model_requests"),
    )


def parse_usage(reader: Iterable[dict[str, str]]) -> ParsedFile:
    parsed = ParsedFile(source_type="openai_usage_csv", provider=PROVIDER)
    for line, row in enumerate(reader, start=2):
        if not row.get("model", "").strip() and not row.get("num_model_requests", "").strip():
            parsed.skipped_empty += 1
            continue
        try:
            parsed.usage.append(_usage_row(row))
        except (RowError, KeyError) as exc:
            parsed.rejected.append(RejectedRow(line, str(exc)))
    return parsed


def _split_line_item(line_item: str) -> tuple[str | None, str, str | None]:
    """'gpt-5-mini-2025-08-07, cached input' -> (model, 'token', 'input_cached')."""
    model, sep, kind = line_item.rpartition(", ")
    if sep and kind in LINE_ITEM_TOKEN_TYPES:
        return model, "token", LINE_ITEM_TOKEN_TYPES[kind]
    if sep:
        return model, kind, None  # a per-model item we don't price separately (yet)
    return None, line_item, None  # account-level item, e.g. "file search tool calls"


def parse_cost(reader: Iterable[dict[str, str]]) -> ParsedFile:
    parsed = ParsedFile(source_type="openai_cost_csv", provider=PROVIDER)
    ungrouped_lines: list[int] = []
    for line, row in enumerate(reader, start=2):
        amount_raw = row.get("amount_value", "").strip()
        line_item = row.get("line_item", "").strip()
        if not amount_raw and not line_item:
            parsed.skipped_empty += 1
            continue
        if not line_item:
            ungrouped_lines.append(line)
            continue
        try:
            if row.get("amount_currency", "").strip().lower() != "usd":
                raise RowError(f"unsupported currency {row.get('amount_currency')!r}")
            amount = parse_decimal(amount_raw)
            if amount is None:
                raise RowError("missing amount_value")
            model, item, token_type = _split_line_item(line_item)
            parsed.costs.append(
                CostRecord(
                    provider=PROVIDER,
                    day=_day(row),
                    scope=ScopeRef(
                        kind="project",
                        natural_id=row.get("project_id", "").strip(),
                        name=_opt(row, "project_name"),
                    ),
                    api_key=None,
                    model_name=model,
                    line_item=item,
                    token_type=token_type,
                    amount_usd=amount,
                )
            )
        except (RowError, NumberFormatError) as exc:
            parsed.rejected.append(RejectedRow(line, str(exc)))

    if ungrouped_lines and not parsed.costs:
        raise UnsupportedFileError(
            "This OpenAI cost export is not grouped. Export again with 'Group by: Line item' "
            "(and project), CSV, 1 day interval."
        )
    for line in ungrouped_lines:
        parsed.rejected.append(RejectedRow(line, "row without line_item in a grouped export"))
    return parsed


def _opt(row: dict[str, str], column: str) -> str | None:
    value = row.get(column, "").strip()
    return value or None
