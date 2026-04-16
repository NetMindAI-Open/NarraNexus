---
code_file: src/xyz_agent_context/agent_framework/provider_resolver.py
stub: false
last_verified: 2026-04-16
---

# Intent

Single arbiter — called once per authenticated HTTP request by
`backend.auth.auth_middleware` — that decides which LLMConfig feeds the
request's ContextVar and whether quota bookkeeping applies.

## Four-branch decision

1. `is_enabled() == False` -> strict no-op. Must not even call
   `user_provider_svc.get_user_config()`. Local mode / feature-off stays
   on the existing `llm_config.json` global fallback path; auth_middleware's
   resolver call is transparent.
2. User has a complete own config (all three slots, all non-empty models,
   all referenced providers active) -> route "user": convert LLMConfig
   into the three dataclasses `set_user_config` expects and inject; tag
   `provider_source="user"`. Quota is NOT consulted.
3. User incomplete + system enabled + `quota_svc.check()==True` -> route
   "system": inject the system LLMConfig as three dataclasses; tag
   `provider_source="system"`. cost_tracker's post-call hook will deduct.
4. User incomplete + system enabled + no budget -> raise
   `QuotaExceededError`; auth_middleware translates to HTTP 402 with
   `error_code: QUOTA_EXCEEDED_NO_USER_PROVIDER`.

## Why "all-or-nothing" for the user-complete check (MVP)

Users with partial config (e.g. agent slot set but embedding not) get
the system path. A future iteration could merge partial user config with
system config slot-by-slot; swap `_is_user_config_complete` for a merger
without changing the branch-shape of `resolve_and_set`.

## Why LLMConfig -> 3 dataclasses conversion lives here

`api_config.set_user_config` accepts three dataclasses (ClaudeConfig +
OpenAIConfig + EmbeddingConfig), not LLMConfig. The mapping
slot->protocol->dataclass is the same one `get_user_llm_configs` does
for AgentRuntime's owner-lookup path. We duplicate the shape here
intentionally — resolver's mapping is authoritative for the HTTP
request path, that function is authoritative for the agent-owner path
(background trigger / MCP tools). They share no runtime state.

## Gotchas

- Branch A must be the FIRST check. Calling `get_user_config` on every
  request in local mode would be a wasted DB round-trip and introduce
  behavioural drift.
- The conversion assumes the agent slot provider is an Anthropic-protocol
  provider and the helper_llm / embedding slots point at OpenAI-protocol
  providers. `_is_user_config_complete` does not assert the protocol
  matches — SLOT_REQUIRED_PROTOCOLS validation lives elsewhere. If a user
  wires a cross-protocol slot, the dataclass conversion will still run
  but downstream LLM SDKs may reject the resulting config.
- `QuotaExceededError` propagates up the middleware stack uncaught by
  resolver. auth_middleware must catch it explicitly and emit 402. If
  any other caller invokes `resolve_and_set` directly, it MUST handle
  `QuotaExceededError` or let it propagate.
