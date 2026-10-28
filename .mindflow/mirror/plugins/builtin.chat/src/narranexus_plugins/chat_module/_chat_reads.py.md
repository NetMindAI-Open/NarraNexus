---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/_chat_reads.py
last_verified: 2026-08-17
stub: false
---

# _chat_reads.py — get_chat_history 的共享实现（AgentDataStore seam 单点）

## 为什么存在（PR-10）

`fetch_chat_history` 是 get_chat_history 工具的唯一实现，从 [[_chat_mcp_tools]] hoist 出来，
让 seam 的 DirectStore（本地）与 backend 孪生路由 [[chat_history]]（云）都调它 → byte-identical，
云 mcp 容器无需 db 凭据。

## 两处刻意改动

1. **de-raw（铁律 #6）**：旧工具用 MySQL 专用 `information_schema` 检查表存在 + 裸
   `SELECT `memory` FROM `{table}```，SQLite 上直接报错。表 `instance_json_format_memory_chat`
   已注册 schema_registry，auto_migrate 保证存在——删存在性检查、改 `db.get_one`（双方言安全，
   先例 chat_history.py get_simple_chat_history）。
2. **instance 归属校验（闭 IDOR）**：旧工具只收 `instance_id`，任何 agent 猜到 `chat_xxx`
   就能读别的 agent 的会话。现加 `agent_id`（LLM 传，同 reply_owner 先例），
   instance 必须属于该 agent；外来/未知 instance 读作**空历史**（无存在性 oracle，与自己没消息的
   instance 不可区分）。孪生路由再 owner-gate agent_id → 云调用方必须拥有该 agent。
   - **残留面（有意，勿在此模式上挂更敏感读）**：agent_id 是 LLM 自报、DirectStore 本地路径无门禁，故本地只把攻击从「猜 instance_id」收紧到「猜匹配的 (agent_id,instance_id)」——变好非关闭；云端 assert_owned 关**跨用户**，同 owner 名下 agent A 读 agent B 会话仍可（owner 本就有全部数据访问权，可接受）。

## 契约

返回工具原有 dict 形状；从不抛（所有失败是 in-band dict）。`limit<=0`=全部，否则取最近 limit 条
（`messages[-limit:]`）。**这不是对旧工具的 byte-parity**（旧的有 IDOR、SQLite 会炸）——parity
是指 DirectStore==孪生路由，两者都闭 IDOR、都双方言安全。
