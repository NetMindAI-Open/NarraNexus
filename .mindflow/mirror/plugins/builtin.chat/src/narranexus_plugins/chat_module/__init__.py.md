---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/__init__.py
last_verified: 2026-09-04
stub: false
---

# chat_module/__init__.py — package surface

从 [[_chat_reads]] re-export `fetch_chat_history`，供 seam 的 [[store]] DirectStore、
backend 孪生路由 [[chat_history]]、测试 import 包而非私有叶子（同 job/awareness 先例）。

从 [[_chat_writes]] re-export `build_bootstrap_greeting_row` + `seed_bootstrap_greeting`
（2026-08-20）：bootstrap 问候行的**唯一定义** + 幂等写入。调用方 = `step_1_select_narrative`
（开局 seed head 实例）与 `ChatModule.persist_turn`（首轮 prepend 兜底）。走包表面而非私有
叶子，正是为了让这个「单写入方」seam 能被 `bootstrap/`/agent_runtime 侧看见地调用（铁律 #3：
`bootstrap/` 不 import chat_module 私有 `_*_impl`）。

故意不 re-export ChatModule 类（module_registry 从 .chat_module 引，避 init-order 耦合）。

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
