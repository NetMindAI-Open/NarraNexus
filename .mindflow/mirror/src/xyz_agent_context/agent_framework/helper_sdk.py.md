---
code_file: src/xyz_agent_context/agent_framework/helper_sdk.py
last_verified: 2026-06-17
stub: false
---

## 2026-06-17 — 改为 protocol-keyed 注册表工厂

`get_helper_sdk` 从 `if _anthropic_helper_ctx set else openai` 的硬编码 if,
改成数据驱动的注册表 `_HELPER_SDK_BY_PROTOCOL = {anthropic, openai}` + 单一
协议决策点 `_resolved_helper_protocol()`。关键不变式:**建 config 的协议 = 选
SDK 的协议**(都源于单点 resolver 给当前 task 装的 helper config),所以 anthropic
卡不可能落到 openai SDK 上——那个错配在结构上无法表达(配合本轮把 legacy
provider_resolver 也并进单点 resolver、4 参 set_user_config、clear 重置全 4 个
ctxvar)。加第三个 helper 协议 = 注册一个 loader + resolver 在 config 上标协议。
未注册协议 → 显式报错,不静默退 openai。设计见
`reference/self_notebook/specs/2026-06-17-helper-sdk-factory-and-single-resolver-design.md`。

# helper_sdk.py — protocol-agnostic helper_llm factory

## Why it exists

Single dispatch point for every helper_llm call site (entity extraction,
narrative update/judge, job lifecycle, memory consolidation, chat
fallback reply, instance decision). Before 2026-06-10 those ~15 sites
constructed `OpenAIAgentsSDK()` directly, hard-binding the helper to the
OpenAI protocol; the factory makes the helper swappable per iron rule #9
and is what lets a single Claude key serve both slots.

## How dispatch works

`get_helper_sdk()` reads the CURRENT asyncio task's
`_anthropic_helper_ctx` ContextVar (set by `set_user_config` from
`RuntimeLLMConfigs.anthropic_helper`):

- ctx set → `AnthropicHelperSDK` (Messages API)
- ctx None → `OpenAIAgentsSDK` (Chat Completions) — the default and the
  unchanged path for every existing OpenAI-helper user

Imports are lazy inside the function to avoid a circular import at
module load (both SDK modules import api_config).

## Gotchas

- New helper call sites MUST go through this factory — grep gate:
  `OpenAIAgentsSDK(` should not appear outside openai_agents_sdk.py /
  helper_sdk.py / tests.
- Dispatch is per-task and resets on every `set_user_config` call
  (passing no anthropic_helper clears the ctx), so multi-tenant
  concurrent turns cannot leak each other's helper choice.
