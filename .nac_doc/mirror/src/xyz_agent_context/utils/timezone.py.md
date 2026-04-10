# timezone.py

Timezone utilities — a consistent layer for UTC storage, user-timezone display, and LLM-friendly time formatting.

## Why it exists

Without a centralized timezone policy, `datetime.now()` calls produce naive datetimes that mix UTC and local time unpredictably across the codebase. SQLite compounds this by returning timestamps as strings. `timezone.py` establishes a single rule: all internal datetimes are UTC (created with `utc_now()`), converted to the user's IANA timezone only at display time (via `to_user_timezone`), and formatted for API responses as ISO 8601 with a `Z` suffix (via `format_for_api`) so JavaScript's `new Date()` parses them correctly.

## Upstream / Downstream

**Consumed by:** `narrative/` (formatting event timestamps for LLM prompts), `backend/routes/` (formatting timestamps in API responses), `module/` implementations that need to express times in the agent's or user's timezone. Re-exported from `utils/__init__.py`.

**Depends on:** stdlib `datetime`, `zoneinfo`. No external libraries.

## Design decisions

**`utc_now()` replaces `datetime.now()`.** Every place in the codebase that needs the current time should call `utc_now()` rather than `datetime.now()`. `utc_now()` returns a timezone-aware UTC datetime, which prevents the common bug of naive datetimes mixing with timezone-aware datetimes in arithmetic.

**`to_user_timezone` handles SQLite string inputs.** SQLite returns datetime columns as strings. Rather than requiring every caller to parse the string first, `to_user_timezone` detects string inputs, strips the trailing `Z` (if any), and calls `datetime.fromisoformat()` before converting. This makes the function safe to call with raw database values.

**`format_for_api` always outputs `Z`-suffixed ISO 8601.** The `Z` suffix is critical for JavaScript interoperability. `new Date("2025-01-15T14:30:00")` (no `Z`) is interpreted as local time in some browsers; `new Date("2025-01-15T14:30:00Z")` is always UTC.

**`format_for_llm` outputs a human-readable format with timezone abbreviation.** LLMs respond better to `"2025/1/15 PM 2:30 (Asia/Shanghai)"` than to ISO 8601. This format is intentionally non-standard because it targets the LLM's language model, not a parser.

**Validation with `is_valid_timezone`.** Timezone strings from user input are validated by attempting to construct a `ZoneInfo` object. Invalid strings produce a descriptive error rather than a runtime `KeyError` later.

## Gotchas

**Naive datetimes are assumed UTC.** Both `to_user_timezone` and `format_for_api` treat naive datetime inputs as UTC by calling `.replace(tzinfo=timezone.utc)`. If a naive datetime was actually created in a local timezone (e.g., by calling `datetime.now()` without UTC), the output will be wrong by the offset of that timezone.

**SQLite timestamp parsing uses `datetime.fromisoformat` which is strict.** Non-ISO strings (e.g., `"Jan 15, 2025"`) in timestamp columns will cause `fromisoformat` to raise `ValueError`, causing `to_user_timezone` to return `None` and `format_for_api` to return `None` or the raw string. The column must contain ISO 8601 values for these functions to work correctly.

**New-contributor trap.** `format_for_llm` returns the string `"Time unknown"` when `dt` is `None` rather than raising an error or returning `None`. Callers that check `if result:` will get truthy behavior for a missing time, which can mask missing data in prompt assembly.
