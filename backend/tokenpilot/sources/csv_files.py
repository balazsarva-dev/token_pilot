"""Detect which provider export a CSV is, and dispatch to the right parser.

Detection uses column *names*, never positions, so column reordering by a provider
does not silently misread data.
"""

import csv
import io
from collections.abc import Callable

from tokenpilot.sources import anthropic_csv, openai_csv
from tokenpilot.sources.records import ParsedFile, UnsupportedFileError

Parser = Callable[[csv.DictReader], ParsedFile]

# (source_type, columns that must all be present, parser)
FORMATS: list[tuple[str, frozenset[str], Parser]] = [
    (
        "openai_usage_csv",
        frozenset({"start_time", "end_time", "model", "num_model_requests", "input_tokens"}),
        openai_csv.parse_usage,
    ),
    (
        "openai_cost_csv",
        frozenset({"start_time", "end_time", "amount_value", "amount_currency", "line_item"}),
        openai_csv.parse_cost,
    ),
    (
        "anthropic_usage_csv",
        frozenset({"usage_date_utc", "model_version", "usage_output_tokens"}),
        anthropic_csv.parse_usage,
    ),
    (
        "anthropic_cost_csv",
        frozenset({"usage_date_utc", "model", "token_type", "cost_usd", "cost_type"}),
        anthropic_csv.parse_cost,
    ),
]


def parse_csv_text(text: str) -> ParsedFile:
    reader = csv.DictReader(io.StringIO(text))
    header = frozenset(name.strip() for name in (reader.fieldnames or []))
    for source_type, required, parser in FORMATS:
        if required <= header:
            parsed = parser(reader)
            parsed.source_type = source_type
            return parsed
    expected = "; ".join(f"{name}: {', '.join(sorted(cols))}" for name, cols, _ in FORMATS)
    raise UnsupportedFileError(f"Unrecognized export format. Expected one of — {expected}")


def parse_csv_bytes(data: bytes) -> ParsedFile:
    # utf-8-sig strips a byte-order mark if a spreadsheet tool added one.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileError("File is not UTF-8 text") from exc
    return parse_csv_text(text)
