---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/_chat_mcp_tools.py
last_verified: 2026-09-04
---

## 2026-08-30 — `reply_owner` 描述里的「private」跟着宪法改口

宪法第 1 条从「plain text 是私有独白」改成「plain text 是可见的工作叙述」
（B 路径）。本文件的工具描述原话是「Your plain text is **private reasoning**
— the owner never receives it」，前半句从此与宪法矛盾。

只改前半句：`private reasoning` → `working narration`。
**「the owner never receives it」原样保留**——它仍然成立（可见 ≠ 投递），而且
它才是这句话存在的理由：解释为什么非调这个工具不可。

## 2026-08-10 (PR-10) — get_chat_history 迁 AgentDataStore seam

工具改为 `get_agent_data_store().get_chat_history(agent_id, instance_id, limit)`，实现下沉到
[[_chat_reads]] `fetch_chat_history`（DirectStore/孪生路由同源）。**三处随迁变化**：
1) 工具签名加 `agent_id`（LLM 传，同 send_message_to_user_directly），闭掉旧的「任意 instance_id
   读别人会话」IDOR；prompts 示例同步加 agent_id。
2) 旧的 `information_schema` MySQL 专用存在性检查 + 裸 SQL 删除（de-raw，双方言安全）——下方
   2026-04-10 Gotcha 里「SQLite 会报错」「直接查表是技术债」两条**已解决**，留作历史。
3) `create_chat_mcp_server` 去掉 `get_db_client_fn` 参数（get_chat_history 走 seam 自解析 db、
   send_message 不碰 db，该参数已死）；调用方 [[chat_module]] create_mcp_server 同步改。


# _chat_mcp_tools.py — ChatModule MCP 工具定义

## 为什么存在

从 `chat_module.py` 分离出来（2026-03-06），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期逻辑解耦。`chat_module.py` 专注于记忆管理，这个文件专注于"Agent 如何输出给用户"。

提供两个工具：
- `reply_owner`：Agent 向用户说话的**唯一通道**，没有 DB 操作，只返回确认
- `get_chat_history`：经 AgentDataStore seam 取会话历史（实现见 [[_chat_reads]]；2026-08-10 前是直查裸表，见 dated section）

## 上下游关系

- **被谁用**：`ChatModule.create_mcp_server()` 调用 `create_chat_mcp_server(port)` 创建 FastMCP 实例；`ModuleRunner` 把返回的 mcp 对象部署为服务器
- **依赖谁**：get_chat_history 走 [[store]] AgentDataStore seam（DirectStore 自解析 db）；实现在 [[_chat_reads]]（`db.get_one`，已 de-raw，不再直查裸表）

## `agent_id` 如何传入

两个工具都要求 Agent 在调用时传入 `agent_id` 和/或 `user_id`。这是因为 MCP 工具运行在独立进程/线程里，没有"当前 agent 上下文"，必须由 LLM 明确传入。Agent 在系统提示里被告知自己的 `agent_id`（通过 `BasicInfoModule` prompts）。

## 设计决策

**`reply_owner` 不写 DB**：工具本身只返回一个成功确认，实际的消息展示依赖于 `AgentRuntime` 监听 `ProgressMessage` 里的工具调用，从 `arguments.content` 里提取内容发给前端 WebSocket。DB 写入在 `ChatModule.after_turn` 里完成（提取该工具的调用内容作为 assistant 消息）。

**~~`get_chat_history` 直接查表而不走 Repository~~（2026-08-10 已改）**：曾经直查动态命名表 `instance_json_format_memory_chat` 是权宜之计+技术债。现已迁 [[_chat_reads]]，用 `db.get_one`（双方言安全），表名仍是该模块常量但不再有裸 SQL。

**工厂函数模式**：`create_chat_mcp_server(port)` 是工厂函数而非类方法（2026-08-10 去掉了已死的 `get_db_client_fn` 参数）。这是为了在不实例化 `ChatModule` 的情况下创建 MCP 服务器（MCP 进程不持有 Module 实例），同时避免循环引用。

## Gotcha / 边界情况

- **表名硬编码**：`instance_json_format_memory_chat` 是 `EventMemoryModule` 命名约定 `instance_json_format_memory_{module_name}` 的具体化。如果模块名改变，这里也必须同步修改。
- **`check_query` 用 MySQL `information_schema`**：这段表存在性检查代码是 MySQL 专用语法，SQLite 里没有 `information_schema.tables`，会报错。SQLite 环境下 `get_chat_history` 工具会返回错误。

## 新人易踩的坑

- 以为调用 `reply_owner` 就完成了响应——工具本身不推送消息，推送是 `AgentRuntime` 在 agent loop 里监听工具调用并转发给前端 WebSocket 完成的。如果前端没收到消息，先检查 WebSocket 连接，而不是检查这个工具。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
