---
code_file: src/xyz_agent_context/utils/retry.py
last_verified: 2026-05-21
stub: false
---

# retry.py — exponential-backoff retry decorator

## Why it exists

One shared `@with_retry` for transient-failure resilience (DB drops, network
timeouts, flaky external APIs) so call sites don't each hand-roll a retry
loop. Works on both sync and async callables.

## Design decisions

- **Type tuple OR predicate.** Retries when the exception matches the
  `exceptions` tuple OR an optional `retry_on(exc) -> bool` predicate returns
  True. The predicate exists because some retry-worthy conditions aren't a
  stable exception class — e.g. an HTTP 429, which different OpenAI-compatible
  aggregators (DeepSeek / Yunwu / SiliconFlow) raise as different types. A
  caller passes a duck-typing predicate instead of importing every SDK's
  rate-limit class (铁律 #9 — no hard SDK coupling). Anything matched by
  neither propagates immediately.
- **Catch `Exception`, re-raise the non-retryable.** The wrappers catch broad
  `Exception` then `raise` when neither matcher fires, so `BaseException`
  (KeyboardInterrupt, CancelledError) is never swallowed and non-retryable
  errors still surface on the first attempt — same observable behavior as the
  old `except exceptions` form.
- **Exponential backoff with cap.** `delay * backoff**(n-1)`, clamped to
  `max_delay`, so retries don't avalanche.

## Gotchas

- `exceptions=()` (empty tuple) is valid — `isinstance(e, ())` is always
  False, so retry is then driven purely by `retry_on`.
- Tests that patch `asyncio.sleep` to skip the backoff must use a plain no-op
  coroutine, NOT one that calls `asyncio.sleep` again (infinite recursion).
