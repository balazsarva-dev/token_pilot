"""Anthropic Admin API: usage report (messages) and cost report, 1-day buckets.

Differences from the Console exports, observed on a real organization:
- keys and workspaces come as ids; we look up their names, because the usage export
  identifies them by name only and both inputs must land on the same ledger rows;
- the default workspace is workspace_id null (the exports call it "Default");
- cost amounts are decimal strings in *cents* and are not rounded per row;
- at most 5 group_by dimensions: key, workspace, model, service tier and speed.
  inference_geo is not among them, so US-only inference (1.1x) is costed as standard;
  the reconciliation against the billed cost shows such a gap.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from tokenpilot.money import NumberFormatError, parse_decimal
from tokenpilot.sources.anthropic_csv import pricing_tier
from tokenpilot.sources.http import JsonClient, SyncError, Transport, urllib_transport
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
BASE_URL = "https://api.anthropic.com/v1/organizations"
API_VERSION = "2023-06-01"
MAX_DAILY_BUCKETS = 31
DEFAULT_WORKSPACE = "Default"
CENTS = Decimal(100)

COST_TOKEN_TYPES = {
    "uncached_input_tokens": "input_uncached",
    "cache_read_input_tokens": "input_cached",
    "cache_creation.ephemeral_5m_input_tokens": "cache_write_short",
    "cache_creation.ephemeral_1h_input_tokens": "cache_write_long",
    "output_tokens": "output",
}


class RowError(ValueError):
    pass


def client(admin_key: str, transport: Transport = urllib_transport, **kwargs) -> JsonClient:
    headers = {"x-api-key": admin_key, "anthropic-version": API_VERSION}
    return JsonClient("Anthropic", BASE_URL, headers, "ANTHROPIC_ADMIN_KEY", transport, **kwargs)


def fetch(api: JsonClient, start: date, end: date) -> ParsedFile:
    """Usage and billed cost for the days start..end (inclusive, UTC)."""
    window = [
        ("starting_at", f"{start.isoformat()}T00:00:00Z"),
        ("ending_at", f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z"),
        ("bucket_width", "1d"),
        ("limit", MAX_DAILY_BUCKETS),
    ]
    keys = _list(api, "/api_keys")
    workspaces = _list(api, "/workspaces", [("include_archived", "true")])
    parsed = ParsedFile(source_type="anthropic_api", provider=PROVIDER)
    parsed.known_keys = [_key(key_id, keys) for key_id in keys]

    usage_groups = ("api_key_id", "workspace_id", "model", "service_tier", "speed")
    params = window + [("group_by[]", g) for g in usage_groups]
    for n, (day, result) in enumerate(_results(api, "/usage_report/messages", params), 1):
        try:
            record = _usage(day, result, keys, workspaces)
        except (RowError, KeyError, TypeError) as exc:
            parsed.rejected.append(RejectedRow(n, f"usage: {exc}"))
            continue
        if record is None:
            parsed.skipped_empty += 1
        else:
            parsed.usage.append(record)

    params = window[:2] + [("limit", MAX_DAILY_BUCKETS)]
    params += [("group_by[]", g) for g in ("workspace_id", "description")]
    for n, (day, result) in enumerate(_results(api, "/cost_report", params), 1):
        try:
            record = _cost(day, result, workspaces)
        except (RowError, NumberFormatError, KeyError, TypeError) as exc:
            parsed.rejected.append(RejectedRow(n, f"cost: {exc}"))
            continue
        if record is None:
            parsed.skipped_empty += 1
        else:
            parsed.costs.append(record)
    return parsed


def _usage(day: date, r: dict, keys: dict[str, dict], workspaces: dict[str, dict]):
    cache = r.get("cache_creation") or {}
    tokens = TokenCounts(
        input_uncached=_count(r, "uncached_input_tokens"),
        input_cached=_count(r, "cache_read_input_tokens"),
        cache_write_short=_count(cache, "ephemeral_5m_input_tokens"),
        cache_write_long=_count(cache, "ephemeral_1h_input_tokens"),
        output=_count(r, "output_tokens"),
    )
    if tokens == TokenCounts():
        return None
    if not r.get("model"):
        raise RowError("result without a model")
    return UsageRecord(
        provider=PROVIDER,
        day=day,
        model_name=r["model"],
        api_key=_key(r.get("api_key_id"), keys),
        scope=_workspace(r.get("workspace_id"), workspaces),
        pricing_tier=pricing_tier(
            r.get("speed") or "", r.get("inference_geo") or "", r.get("service_tier") or ""
        ),
        tokens=tokens,
        requests=None,  # the usage report has no request counts either
    )


def _cost(day: date, r: dict, workspaces: dict[str, dict]) -> CostRecord | None:
    if str(r.get("currency", "")).upper() != "USD":
        raise RowError(f"unsupported currency {r.get('currency')!r}")
    cents = parse_decimal(str(r.get("amount", "")))
    if cents is None:
        raise RowError("missing amount")
    if cents == 0:
        return None
    if r.get("cost_type") == "tokens":
        raw_type = r.get("token_type")
        if raw_type not in COST_TOKEN_TYPES:
            raise RowError(f"unknown token_type {raw_type!r}")
        line_item, token_type, model = "token", COST_TOKEN_TYPES[raw_type], r.get("model")
    else:
        line_item, token_type, model = r.get("cost_type") or "unknown", None, None
    return CostRecord(
        provider=PROVIDER,
        day=day,
        scope=_workspace(r.get("workspace_id"), workspaces),
        api_key=None,  # the cost report cannot be grouped by key
        model_name=model,
        line_item=line_item,
        token_type=token_type,
        amount_usd=cents / CENTS,
    )


def _key(key_id: str | None, keys: dict[str, dict]) -> KeyRef:
    if not key_id:
        return KeyRef(natural_id="")  # Console / Workbench usage
    key = keys.get(key_id, {})
    name = key.get("name")
    # The exports know keys by name only; use it as the natural id so both inputs converge.
    return KeyRef(natural_id=name or key_id, key_id=key_id, name=name, status=key.get("status"))


def _workspace(workspace_id: str | None, workspaces: dict[str, dict]) -> ScopeRef:
    if not workspace_id:
        return ScopeRef(kind="workspace", natural_id=DEFAULT_WORKSPACE, name=DEFAULT_WORKSPACE)
    name = workspaces.get(workspace_id, {}).get("name")
    return ScopeRef(kind="workspace", natural_id=name or workspace_id, name=name)


def _results(api: JsonClient, path: str, params: list[tuple[str, object]]):
    for page in api.pages(path, params):
        for bucket in page.get("data", []):
            start = datetime.fromisoformat(bucket["starting_at"])
            end = datetime.fromisoformat(bucket["ending_at"])
            if end - start != timedelta(days=1):
                raise SyncError(f"Anthropic: expected 1-day buckets, got {bucket['starting_at']}")
            for result in bucket.get("results", []):
                yield start.date(), result


def _list(api: JsonClient, path: str, extra: list[tuple[str, object]] | None = None):
    """Paginated list endpoint (after_id cursor) -> {id: item}."""
    items, after = {}, None
    while True:
        params = [("limit", 100), *(extra or [])] + ([("after_id", after)] if after else [])
        body = api.get(path, params)
        items.update({item["id"]: item for item in body.get("data", [])})
        after = body.get("last_id")
        if not body.get("has_more") or not after:
            return items


def _count(values: dict, name: str) -> int:
    value = values.get(name) or 0
    if isinstance(value, bool) or not isinstance(value, int | Decimal) or value < 0:
        raise RowError(f"{name}: not a non-negative whole number: {value!r}")
    if value != int(value):
        raise RowError(f"{name}: not a whole number: {value!r}")
    return int(value)
