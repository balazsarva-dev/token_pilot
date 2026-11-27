"""Provider-neutral records produced by every source adapter (CSV now, API later)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class KeyRef:
    """An API key as seen in an export. natural_id = "" means no key (e.g. playground)."""

    natural_id: str
    key_id: str | None = None
    name: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class ScopeRef:
    """OpenAI project or Anthropic workspace. natural_id = "" means none/default."""

    kind: str
    natural_id: str
    name: str | None = None


@dataclass(frozen=True)
class TokenCounts:
    """Disjoint counters: input_uncached + input_cached = all input tokens."""

    input_uncached: int = 0
    input_cached: int = 0
    cache_write_short: int = 0  # Anthropic 5m, OpenAI 30m
    cache_write_long: int = 0  # Anthropic 1h, OpenAI 12h
    output: int = 0

    def __add__(self, other: "TokenCounts") -> "TokenCounts":
        return TokenCounts(
            self.input_uncached + other.input_uncached,
            self.input_cached + other.input_cached,
            self.cache_write_short + other.cache_write_short,
            self.cache_write_long + other.cache_write_long,
            self.output + other.output,
        )


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    day: date
    model_name: str  # raw name from the export; resolved to dim_model later
    api_key: KeyRef
    scope: ScopeRef
    pricing_tier: str  # standard / batch / anything else = unpriced in v1
    tokens: TokenCounts
    requests: int | None


@dataclass(frozen=True)
class CostRecord:
    provider: str
    day: date
    scope: ScopeRef
    api_key: KeyRef | None  # OpenAI cost exports carry no key
    model_name: str | None  # None for non-token items (tool calls, web search)
    line_item: str  # "token" or the provider's item name
    token_type: str | None  # one of TokenCounts' field names, None for non-token items
    amount_usd: Decimal


@dataclass(frozen=True)
class RejectedRow:
    line: int  # 1-based line number in the file, header = line 1
    reason: str


@dataclass
class ParsedFile:
    source_type: str
    provider: str
    usage: list[UsageRecord] = field(default_factory=list)
    costs: list[CostRecord] = field(default_factory=list)
    # Keys/scopes seen only in zero-amount rows: no fact, but they still describe the account.
    seen_keys: list[tuple[date, KeyRef]] = field(default_factory=list)
    # Keys the provider lists (API sync): enrich names / status of keys we already know.
    known_keys: list[KeyRef] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    skipped_empty: int = 0  # rows for days without usage


class UnsupportedFileError(ValueError):
    """The whole file cannot be imported (unknown format, wrong export settings)."""
