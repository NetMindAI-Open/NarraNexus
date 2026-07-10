---
code_file: src/xyz_agent_context/agent_framework/anthropic_helper_sdk.py
last_verified: 2026-07-03
stub: false
---

## 2026-07-03 — de-silence missing usage (Phase 0 / module H)

Both cost sites (structured `llm_function` + stream) now call
`cost_tracker.warn_missing_usage(...)` when a live cost context exists but the
provider returned no usage, instead of silently skipping the record.



## 2026-06-17 — llm_function 必须内部 streaming(否则大输入必挂)

`llm_function` 原来用非流式 `client.messages.create`。Anthropic SDK 有个守卫:
当 `max_tokens` 大到可能让操作 >10 分钟时,非流式请求直接抛
`ValueError: Streaming is required ...`。helper 的输入动辄几万 token(narrative
continuity / consolidation / entity update),于是 anthropic-helper 槽的**每个
结构化 helper 调用都挂**。改为内部 `messages.stream().get_final_message()` 收口,
`resp` 形状(content+usage)不变、调用方零改动。这个 bug 是被单点 resolver 重构
**暴露**的——以前 anthropic-helper 用户被错误路由到 OpenAI SDK,根本走不到这里
(见 helper_sdk.py.md 2026-06-17)。线上实测:claude_code agent + anthropic helper
一轮 3× `llm_function ok`(修前 5× failed),记忆正确落地。
# anthropic_helper_sdk.py — Anthropic-protocol helper_llm client

## Why it exists

The helper_llm slot was historically OpenAI-protocol-only, which made a
single Claude key insufficient to run the platform (agent slot ✓, helper
slot ✗). This SDK is the Messages-API counterpart of `OpenAIAgentsSDK`'s
helper interface, enabling "one Claude key serves agent AND helper" —
the core of the 2026-06-10 one-key onboarding feature.

## Upstream / downstream

- **Selected by**: `helper_sdk.get_helper_sdk()` — call sites never
  import this class directly. Dispatch keys off the per-task
  `_anthropic_helper_ctx` ContextVar (set by `set_user_config` when the
  resolver produced an `AnthropicHelperConfig`).
- **Reads**: `api_config.anthropic_helper_config` proxy (api_key /
  base_url / model / auth_type).
- **Reuses from openai_agents_sdk**: `_SimpleResult` / `_ParsedResult`
  (so downstream consumers see identical result shapes),
  `_extract_json_from_llm_output`, and the `_last_llm_call_info`
  ContextVar — deliberate import of that module's internals to keep the
  two helpers' observable behavior in lock-step.
- **Cost**: same `record_cost` / `get_cost_context` hooks as the OpenAI
  helper (`call_type` llm_function / llm_stream).

## Design decisions

- **Structured output is prompt-engineered only.** Anthropic's Messages
  API has no `response_format` / json_schema parameter — the schema is
  appended to the system prompt and the reply is extracted + validated
  client-side, identical to the OpenAI helper's level-3 "prompt_only"
  fallback. `_last_llm_call_info.structured` reports
  `"anthropic_prompt"` on the parsed path.
- **Per-call `model=` overrides are IGNORED** (`_resolve_model`): call
  sites configure OpenAI-flavored names (narrative judge's
  gpt-5.4-mini etc.) which don't exist on Anthropic endpoints. The slot
  model always wins; "default" sentinel falls back to the dataclass
  default (claude-haiku-4-5).
- **`reasoning_effort` is accepted and clamped** (debug log, never an
  error — iron rule #15); the Messages API has no per-call effort knob.
- **`max_tokens` is mandatory** on the Messages API: resolved from
  model_catalog's per-model cap, falling back to 4096 (helper outputs
  are small).
- **auth_type "bearer_token"** (NetMind anthropic row) maps to the SDK's
  `auth_token` param (Authorization: Bearer); everything else uses
  standard `api_key` (x-api-key header).

## Gotchas

- Tests stub `_build_client` (monkeypatch) rather than the anthropic
  package — see tests/agent_framework/test_one_key_onboarding.py.
- Keep the public surface (llm_function / llm_stream signatures, result
  wrapper attributes) byte-compatible with OpenAIAgentsSDK; ~15 call
  sites are dispatch-blind through the factory.
