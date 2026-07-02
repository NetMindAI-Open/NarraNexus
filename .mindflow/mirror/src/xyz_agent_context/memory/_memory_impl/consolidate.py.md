---
code_file: src/xyz_agent_context/memory/_memory_impl/consolidate.py
last_verified: 2026-06-14
stub: false
---

# consolidate.py — unified Agent Memory

## 2026-06-14 — systemic-error 检测补 anthropic 协议(PR #25 §5)

`_is_systemic_llm_error` 原本只 isinstance `openai.*` + 看 `status_code`。但
helper SDK 走 anthropic 协议时返回 AnthropicHelperSDK,其 `anthropic.APIConnectionError`
**既不是 openai.\* 类、也没有 `status_code`**(连接在拿到 HTTP 响应前就断了)→
两个检查都漏 → 落回 bisect-drop 丢 facts(正是 2026-06-11 那个 P0 对 anthropic-helper
用户重新打开)。修法:抽出 `@lru_cache` 的 `_systemic_exc_types()`,懒导入 anthropic
(openai-only 部署可能没装),把两家 SDK 的 auth/permission/rate-limit/connection/5xx
类一起纳入 isinstance。测试见 test_consolidate_systemic_errors.py 的
`test_classifier_catches_anthropic_connection_error` 等。

LLM consolidation (9 processing rules, Hindsight-derived) distilling raw units into evolving observations, with adaptive-bisect resilience. Fully LLM+SQL, no vectors.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

## 2026-06-11 — systemic errors raise; only content errors bisect

P0 regression fix: the bisect-and-drop resilience treated ALL exceptions
as content errors, so the cloud worker's credential-less 401s bisected
to single facts and permanently dropped 4599 of them. New
`_is_systemic_llm_error` (auth / permission / rate-limit / connection /
5xx, walking the __cause__ chain because the Agents SDK wraps client
errors) → `SystemicLLMError` raises out of consolidate so the worker
isolates the scope with facts intact. Content/parse failures keep the
bisect-to-isolation policy unchanged.

