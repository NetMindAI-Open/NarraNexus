---
code_file: src/xyz_agent_context/agent_framework/system_provider_service.py
stub: false
last_verified: 2026-04-16
---

# Intent

Module-level singleton that reads `SYSTEM_DEFAULT_LLM_*` env vars once at
first `instance()` call and exposes a fixed, cloud-only `LLMConfig` that
represents the NetMind (or equivalent) account backing the free tier for
newly registered users.

## Upstream
- ProviderResolver — calls `is_enabled()` as branch-A short-circuit, then
  `get_config()` to inject the system LLMConfig into the request's
  ContextVar when the user has no personal provider and has budget.
- QuotaService.init_for_user — calls `is_enabled()` to decide whether to
  seed a quota row, and `get_initial_quota()` to read the initial token
  counts the new row is stamped with.
- App lifespan — calls `instance()` once at startup so env reads happen
  on a controlled thread, not mid-request.

## Downstream
- `schema/provider_schema.py` — LLMConfig / ProviderConfig / SlotConfig
  / ProviderSource / ProviderProtocol / AuthType

## Gating rules (all must hold for is_enabled() == True)
1. Cloud mode (`DATABASE_URL` non-sqlite OR `DB_HOST` set)
2. `SYSTEM_DEFAULT_LLM_ENABLED=true` (case-insensitive)
3. `SYSTEM_DEFAULT_LLM_API_KEY` non-empty after strip
4. All three slot model env vars present and non-empty
5. `SYSTEM_DEFAULT_LLM_SOURCE` parses as a ProviderSource enum value

Any failure leaves `_enabled=False` and `_config=None`; `get_config()`
will raise, which is intentional — callers should guard on
`is_enabled()` first.

## Design decisions
- Two ProviderConfig entries sharing the same api_key and `linked_group`
  capture NetMind's "one key, two protocols" shape. `auth_type` differs
  because NetMind accepts Anthropic via Bearer token but OpenAI via
  standard API key.
- `supports_anthropic_server_tools=False` on the anthropic provider —
  NetMind proxies but does not execute server-side tools like
  `web_search_20250305`; the tool-policy guard layer uses this flag.
- Singleton with `_instance` class-level cache. The autouse fixture in
  `tests/agent_framework/test_system_provider_service.py` resets this
  between tests so env changes are observed.
- `get_initial_quota()` is callable even when disabled. Reading quota
  env vars costs nothing and the function returns `(0, 0)` by default,
  which is the safe value for disabled systems.

## Gotchas
- Changing any `SYSTEM_DEFAULT_LLM_*` env requires restarting the backend
  process. There is no hot reload by design — a mid-request env change
  would create a config inconsistency window.
- The cloud-mode check must stay in sync with `backend/auth.py`'s
  `_is_cloud_mode()`. Both read `DATABASE_URL` and `DB_HOST` identically.
  Diverging them will create split-brain behaviour where registration
  seeds quota but the resolver does not route system traffic (or vice
  versa).
