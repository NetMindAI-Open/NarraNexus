---
code_file: src/xyz_agent_context/agent_framework/llm_api/embedding.py
last_verified: 2026-05-21
stub: false
---

# embedding.py — OpenAI-compatible embedding client

## Why it exists

The single place that turns text into vectors for the whole platform
(narratives, events, jobs, social entities, RAG). Wraps an `AsyncOpenAI`
client pointed at whatever OpenAI-compatible endpoint the user configured,
plus an in-process cache and cost recording.

## Design decisions

- **Credentials only from explicit args or the per-task ContextVar.** Never
  falls back to env / `llm_config.json` — embedding provider must come from
  the user's configured slot (see `api_config.get_current_embedding_config`).
  Background jobs that have no request ContextVar pass `(base_url, api_key,
  model)` explicitly.
- **Retry covers rate limits, not just network faults.** Both the single and
  batch request methods use the shared `_EMBED_RETRY` policy: the transient
  network tuple `(ConnectionError, TimeoutError, OSError)` PLUS a
  `retry_on=_is_rate_limit_error` predicate, with 5 attempts and a
  few-seconds exponential backoff (2/4/8/16s, capped 30s). A 429 is transient
  and back-off-able; before this, 429 fell through as a hard failure, which
  left the embedding rebuild permanently stuck on the rate-limited rows (they
  stayed "missing" and every rebuild re-hit the same wall).
- **`_is_rate_limit_error` duck-types the 429.** Matches on HTTP status 429,
  then a `ratelimit` class-name substring, then message text
  ("429" / "rate limit" / "too many requests"). Deliberately does not import
  a specific SDK's exception so aggregators that phrase 429 differently are
  still retried (铁律 #9, #15 — the platform doesn't govern the user's
  provider choice, it just stops failing fast on the provider's rate limit).

## Gotchas

- Don't pass `dimensions` to `embeddings.create` — let each model use its
  native output size; passing catalog dims across a model switch causes 400s.
- Bumping `_EMBED_RETRY.max_attempts` also lengthens worst-case latency per
  embed (up to ~30s of backoff) — acceptable for a background rebuild, and
  long runs are first-class (铁律 #14), but keep it in mind for hot paths.
