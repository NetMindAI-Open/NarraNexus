# retry.py

Decorator that adds automatic retry with exponential backoff to any async or sync function.

## Why it exists

Transient failures — database connection drops, MCP service timeouts, LLM API rate limits — are a reality in a system that calls multiple external services. Without structured retry, each callsite either duplicates manual retry loops or lets transient errors propagate as permanent failures. `retry.py` provides a single `@with_retry(...)` decorator that wraps both async and sync functions, adding configurable retry count, delay, and exponential backoff without the caller needing to know the retry details.

## Upstream / Downstream

**Consumed by:** database backends (SQLite write contention), `agent_framework/` (LLM API calls), `module/` (MCP server connectivity), and any other code imported via `utils/__init__.py` (`with_retry`, `DEFAULT_RETRYABLE_EXCEPTIONS`).

**Depends on:** stdlib `asyncio`, `time`, `functools`. No external libraries.

## Design decisions

**Single decorator for both async and sync functions.** `with_retry` inspects the decorated function with `asyncio.iscoroutinefunction()` and returns either `async_wrapper` or `sync_wrapper`. This means the same decorator syntax works for both, reducing the API surface.

**`DEFAULT_RETRYABLE_EXCEPTIONS` covers OS-level network errors.** The default set includes `ConnectionError`, `TimeoutError`, `ConnectionResetError`, `ConnectionRefusedError`, `BrokenPipeError`, and `OSError`. Application-level exceptions (e.g., `ValueError`, `DataGatheringError`) are not in the default set because those indicate programming errors rather than transient conditions — retrying them would be wrong.

**Exponential backoff capped at `max_delay`.** The wait time grows as `delay * backoff^(attempt-1)` but is clamped to `max_delay` (default 60s). This prevents retries from stalling indefinitely for high-attempt configurations.

**`on_retry` callback.** An optional `on_retry(exception, attempt)` callback gives callers a hook for side effects (e.g., logging, metrics, circuit-breaker updates) without coupling them to the retry loop internals.

## Gotchas

**`max_attempts` includes the first attempt.** With `max_attempts=3`, the function is called at most 3 times total: once originally plus 2 retries. This matches the mental model for most callers but differs from libraries where `max_retries` means additional attempts only.

**Exceptions not in the `exceptions` tuple propagate immediately.** If a decorated function raises `ValueError` and `exceptions=(ConnectionError,)`, the `ValueError` is not caught and propagates without any retry. This is correct behavior but can be surprising when an unexpected exception type surfaces from a deep call stack.

**New-contributor trap.** Decorating a function with `@with_retry()` (empty parens) works, but `@with_retry` without parens does not — the decorator factory requires being called. The second form will wrap the function in the outer `decorator` closure and produce an object that is not callable as the function.
