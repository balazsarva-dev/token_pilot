"""Exact number handling. Money and token counts never pass through float."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ONE_MILLION = Decimal(1_000_000)
# Storage precision for money: NUMERIC(20,10).
STORAGE_QUANTUM = Decimal("0.0000000001")


class NumberFormatError(ValueError):
    pass


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a CSV cell into Decimal. Empty cell -> None.

    Handles provider quirks seen in real exports: exponent notation ("0E-6176")
    and very long fractions ("0.009805500000000000000000000000").
    """
    raw = raw.strip()
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise NumberFormatError(f"not a number: {raw!r}") from exc
    if not value.is_finite():
        raise NumberFormatError(f"not a finite number: {raw!r}")
    # Normalize exponent-heavy zeros like 0E-6176 so they fit NUMERIC columns.
    return Decimal(0) if value == 0 else value


def parse_count(raw: str) -> int | None:
    """Parse a token/request count. Exports may write counts as "11512.0"."""
    value = parse_decimal(raw)
    if value is None:
        return None
    if value != value.to_integral_value() or value < 0:
        raise NumberFormatError(f"not a non-negative whole number: {raw!r}")
    return int(value)


def to_storage(amount: Decimal) -> Decimal:
    """Round once, half-up, to the storage precision."""
    return amount.quantize(STORAGE_QUANTUM, rounding=ROUND_HALF_UP)
