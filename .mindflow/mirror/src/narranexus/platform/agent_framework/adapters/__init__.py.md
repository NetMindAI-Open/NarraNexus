---
code_file: src/narranexus/platform/agent_framework/adapters/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 公开 `build_tool_policy_guard`（批 6c，A2-1）

框架适配器自批 6b 起是插件（`builtin.frameworks.claude_code` 有自己的 wheel），
而「谁该装上共享的 PreToolUse 守卫」的答案恰恰就是框架适配器。
所以**构造函数**公开，守卫的内部实现仍然私有。
惰性 re-export：守卫模块会拖起 tool-policy schema，不用它的适配器不该付这个代价。

# agent_framework/adapters/__init__.py — group anchor

Created in the 2026-07-24 agent_framework regrouping (61 flat files →
loop/ adapters/ llm/ providers/ + 2 cross-cutting root files). Agent-framework adapters (binding rule #9's swap seam): one
subpackage per framework (claude/, codex/), the OpenAI-agents caller,
and the shared PreToolUse policy guard.
