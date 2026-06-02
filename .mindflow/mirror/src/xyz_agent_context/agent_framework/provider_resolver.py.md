---
code_file: src/xyz_agent_context/agent_framework/provider_resolver.py
stub: false
last_verified: 2026-06-01
---

# Intent

Single arbiter that decides which LLMConfig feeds a run and whether quota
bookkeeping applies. The decision is now factored into **one verdict-only
classifier** (`ProviderResolver.classify` → `ProviderAvailability`) so every
caller that needs "can this user resolve a usable provider right now" shares
the exact same tree:

- HTTP request path: `resolve` / `resolve_and_set` (auth_middleware) maps the
  verdict to three dataclasses + ContextVars, or to a `ProviderResolverError`.
- Job resume gate: `JobTrigger._user_can_run` maps the verdict via
  `is_runnable` (through the `classify_provider_for_user` wiring helper).

**Why one classifier (2026-06-01):** the resume gate used to reimplement the
tree as "quota OR own-provider-complete" and drifted — it ignored
`prefer_system_override`, so a user opted in to an exhausted free tier who also
had an own provider was judged runnable, resumed, then rejected by the runtime
(which will NOT silently spend their own key), forever. That was the 2026-05-31
prod pause/resume oscillation. Extracting `classify` makes the gate and the
runtime physically incapable of disagreeing.

## The decision tree (`classify`)

Keyed on the user's `prefer_system_override` Settings toggle — the single
source of truth — NOT on whether an own config happens to exist:

0. `is_enabled() == False` -> `SYSTEM_DISABLED` (strict no-op; must not even
   call `quota_svc.get` / `get_user_config`). Local mode / feature-off stays on
   the `llm_config.json` global fallback; `resolve` returns `None`, the resume
   gate treats it as runnable (not gated).
1. `prefer_system_override == True` (default for new users):
   1a. `quota_svc.check()` -> `SYSTEM_OK` (route system; cost_tracker deducts).
   1b. no budget + complete own config -> `FREE_TIER_EXHAUSTED`
       (`resolve` raises `FreeTierExhaustedError`; gate = NOT runnable).
   1c. no budget + no own provider -> `QUOTA_EXCEEDED`
       (`resolve` raises `QuotaExceededError`).
2. `prefer_system_override == False` (or no quota row = implicit opt-out):
   2a. complete own config -> `USER_OK` (route user; quota NOT consulted).
   2b. own config missing/incomplete -> `NO_PROVIDER`
       (`resolve` raises `NoProviderConfiguredError`; opt-out is honoured, no
       silent free-tier fallback).

`is_runnable(verdict)` is True only for `{SYSTEM_OK, USER_OK, SYSTEM_DISABLED}`.

## Service-call order matters

`classify` calls `is_enabled` → `quota.get` → `get_user_config` → `quota.check`
(the last ONLY on the opted-in branch). This order is load-bearing: the
disabled path returns before touching quota/user services (strict no-op
laziness), and the opt-out path never probes quota (the user pays with their
own key). Tests assert these `assert_not_called()` patterns.

## Why "all-or-nothing" for the user-complete check (MVP)

Partial config (e.g. agent slot set but embedding not) counts as incomplete.
A future iteration could merge partial user config with system config
slot-by-slot; swap `_is_user_config_complete` without changing the verdict
shape of `classify`.

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
