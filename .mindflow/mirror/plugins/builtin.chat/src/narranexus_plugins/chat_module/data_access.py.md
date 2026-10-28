---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/data_access.py
last_verified: 2026-09-07
stub: false
---

# chat_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: get_chat_history over `fetch_chat_history`, the same function the chat-history twin route calls. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.

## 2026-09-07 — docstring 去掉 register_all（round-2）

数据访问贡献只由 manifest 在 boot 注册。
