# Provider export formats (observed on real samples)

Observed on real exports in October 2026 (kept locally in `test_ai_costs/`, git-ignored). Volumes are tiny (cents), so they validate **formats**, not savings logic.

## Anthropic — Console "Cost" CSV (`claude_api_cost_<from>_to_<to>.csv`)
Columns: `usage_date_utc, model, workspace, api_key, usage_type, context_window, token_type, cost_usd, list_price_usd, cost_type, inference_geo, speed, api_key_id, api_key_status`

- Grain: day × model × workspace × API key × token_type (`input_no_cache`, `input_cache_read`, `input_cache_write_5m`, `input_cache_write_1h`, `output`) × context window × geo × speed. Non-token rows: `cost_type` = `code_execution`, `web_search`, `session_runtime` with `--` placeholders.
- **Has API key names and ids** (the API returns ids only) → fills `api_key_label`.
- **No token counts** — cost only. Token counts need the Usage export or the Usage API.
- `cost_usd` is **rounded to 2 decimals per row** (many real costs show as `0.00`). Summing rows loses sub-cent amounts → reconciliation from this CSV is only cent-accurate per row; the Cost API (decimal cents) is more precise.
- `list_price_usd` next to `cost_usd`: lets us detect discounts (cost < list).
- Model is a display name (`Claude Sonnet 5.5`), not an API id → `model_alias` must map display names too.
- Placeholders: `--` = not applicable; `list_price_usd` can be empty; `api_key_status` empty.
- Context window values use a Unicode `≤` (`≤ 200k`) → read as UTF-8.

## OpenAI — Usage page "Cost data" CSV, ungrouped (`cost_<from>_<to>.csv`)
Columns: `start_time, end_time, start_time_iso, end_time_iso, amount_value, amount_currency, quantity, quantity_unit, line_item, user_id, api_key_id, project_id, organization_id, project_name, organization_name, user_email, api_source`

- This sample was exported **without grouping**: one row per day with the org total; `line_item`, `project_id`, `api_key_id` empty. Grouped exports fill those columns (to verify with a grouped sample).
- Days without spend still appear as rows with empty amount → treat as "no data", not as an error; do not create 0-cost facts from them.
- `amount_value` has very high precision (`0.009805500000000000000000000000`) → parse straight to `Decimal`, never float.
- **Inconsistent ISO timestamps** within one file: some rows `2026-09-21T00:00:00`, others `2026-09-22T00:00:00+00:00`. Use the Unix `start_time` as the source of truth (always UTC).
- `quantity` / `quantity_unit` exist but are empty in this ungrouped export.

## Anthropic — Console "Usage" CSV (`claude_api_tokens_<from>_to_<to>.csv`)
Columns: `usage_date_utc, model_version, api_key, workspace, usage_type, context_window, usage_input_tokens_no_cache, usage_input_tokens_cache_write_5m, usage_input_tokens_cache_write_1h, usage_input_tokens_cache_read, usage_output_tokens, web_search_count, inference_geo, speed`

- Grain: day × model × API key × workspace × usage type × context window × geo × speed. Token counters map 1:1 onto our disjoint columns (no_cache / cache_write_5m / cache_write_1h / cache_read / output).
- `model_version` is the API model id (`claude-sonnet-5-5`), unlike the Cost CSV's display name.
- API key is a **name only** (no id); workspace is a name (`Default`, `client-a`). Join to the Cost CSV via (date, key name, workspace, model) — the alias table must map display name ↔ model id.
- **No request count** → average tokens per request is not available from Anthropic CSVs. R1 (right-sizing) on Anthropic CSV data can only use total volume and output/input ratios; per-request averages need another source.

## OpenAI — "Activity" CSV, completions (`completions_usage_<from>_<to>.csv`)
Grouped by project + API key + model, 1 day. Columns (28): time columns as in the cost CSV, `project_id, num_model_requests, user_id, api_key_id, model, batch, service_tier, input_tokens, output_tokens, input_cached_tokens, input_cache_write_tokens, input_cache_write_30m_tokens, input_cache_write_12h_tokens, input_uncached_tokens, input_text_tokens, output_text_tokens, input_cached_text_tokens, input_audio_tokens, input_cached_audio_tokens, output_audio_tokens, …`

- **Has `num_model_requests`** → average tokens per request per key/model/day is available (R1 works well on OpenAI data).
- Counts are written as floats (`13.0`, `11512.0`) → parse via `Decimal`, assert integral, store as BIGINT.
- `input_tokens` includes cached; `input_uncached_tokens` is given explicitly — use it, and verify `uncached + cached = input`.
- Cache write buckets are 30m / 12h (different from Anthropic's 5m / 1h) → our cache-write columns must be provider-neutral (e.g. `cache_write_short`, `cache_write_long`) or keyed by duration.
- `batch`, `service_tier` empty in this sample (no batch use). Empty days appear as all-empty rows → skip.

## OpenAI — "Cost" CSV grouped by line item + project
Same columns as the ungrouped export; now `line_item` = `"<model>, input" | ", cached input" | ", output"` or non-token items (`file search tool calls`), `quantity` + `quantity_unit=tokens`, `project_id` + `project_name` filled.

- **`api_key_id` stays empty** even when grouped → per-key cost must be computed (tokens × price), as planned.
- Zero amounts can appear in exponent notation: `0E-6176` → `Decimal` handles it; never pass the raw string to SQL.
- Sanity check on the sample: gpt-5-mini input 11,512 tokens → $0.002878 ($0.25/M), output 12,686 → $0.025372 ($2.00/M). Cost CSV and Activity CSV reconcile at line-item level.

## Still needed
Nothing for M1. (Sample volumes are tiny; savings rules will be tested on generated data.)
