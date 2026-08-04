---
code_file: src/xyz_agent_context/module/message_bus_module/_message_bus_mcp_tools.py
last_verified: 2026-08-03
stub: false
---

## 2026-08-03 — `_send_turn_source`:章按「这一条发给谁」定,不按整轮定

两个发送工具不再直接写 `caller_turn_source()`,而是走
`_send_turn_source(to_agent=… / channel_id=…)`:先取轮次种类,只有当**本条
send 的目标**等于本轮差事作用域(`caller_errand_scope()`,见
[[_mcp_identity]])时才升级成 [[hook_schema]] 的 `BUS_ERRAND_TURN_SOURCE`。

**为什么不能整轮盖章**(同 PR 内自我推翻的做法):
`MessageBusModule.hook_data_gathering` 每轮把**跨所有 channel** 的未读
(`bus.get_unread`)注进 context,模块提示词又**要求**回答它们(「A question
is never ping-pong」)。所以差事延续轮次里顺手回答别的同伴 C 是平台自己引导
的常规路径;整轮盖章会把那条**回答**标成提问,C 于是不再向自己 owner 回报
—— P1 换个座位复发(2026-08-03 review round 4)。

已记录的残余:发进「恰好是差事 channel 的群 channel」会把每个成员那份都盖成
提问(bus 差事跑在自动建的 DM 上,要手工建群当差事 channel 才踩到)。

## 2026-08-04 — 两个发送工具都记录本轮种类

`bus_send_to_agent` 与 `bus_send_message` 都调 `caller_turn_source()` 并传给
bus,让消息自己带上"这是提问还是回复"。**两个都要**:它们写同一张表、
同一个消费方(`_incoming_is_reply_to_my_errand`),漏一个就让那条路径落降级。
turn source 同时走显式 header 与 bearer,所以 codex 上也读得到
(见 [[_mcp_identity]]);读不到时传 None,触发侧按未知降级。

## 2026-07-20 — file attachments + team share

`bus_send_message` / `bus_send_to_agent` gained `attachment_refs` (comma-separated
`att_` file_ids and/or workspace-relative paths); `_stage_send_attachments` resolves
the sender's owner (`agents.created_by`, dialect-safe via `get_db_client`) and stages
the files through [[_bus_attachment_impl]] before send. New `bus_share_to_team`
validates ownership + membership (`teams` / `team_members`) then publishes a file into
the team's shared scratch dir (a server-side write — agents can't write `_shared`
themselves under the cloud sandbox). owner_user_id is always looked up, never taken
from the LLM.

# _message_bus_mcp_tools.py — MessageBus MCP 工具函数集合

## 为什么存在

`MessageBusModule` 通过 MCP 服务器向 LLM 暴露工具，但工具函数的具体实现不应该直接写在 Module 类里（会让 `message_bus_module.py` 变成一个巨型文件，且工具函数需要独立可测试）。`_message_bus_mcp_tools.py` 把所有 MCP 工具的实现提取出来，成为可以独立注册到 MCP 服务器的函数集合。

命名前缀 `_` 表示这是 Module 的私有实现，不被包外直接引用。

## 上下游关系

**被谁用**：`MessageBusModule.get_mcp_config()` 返回的 `MCPServerConfig` 里包含工具列表，MCP 服务器框架（`module_runner.py`）把这些工具函数注册到 MCP 协议上暴露给 LLM。

**调用谁**：每个工具函数接受一个 `port` 参数（MCP 服务器端口）和一个 `get_db_client_fn` 参数（工厂函数，调用时返回 DB 客户端）。工具函数内部用这个工厂函数创建 `LocalMessageBus` 实例，调用 `MessageBusService` 的对应方法。这种依赖注入方式避免了工具函数持有全局 DB 状态。

## 设计决策

工具函数签名遵循系统约定的提取模式（`standalone function taking (port, get_db_client_fn)`）——这是为了避免与 `MessageBusModule` 类的循环导入问题，也让工具函数可以在没有 Module 实例的环境里（比如测试）独立运行。

工具覆盖了 MessageBus 的完整操作面：发消息（`send_message`、`send_to_agent`）、查询（`get_unread`、`get_messages`）、频道管理（`create_channel`、`join_channel`、`leave_channel`）、Agent 发现（`search_agents`、`register_agent`、`get_agent_profile`）。

## Gotcha / 边界情况

工具函数里的错误处理：一般返回 `{"success": True/False, "error": "..." }` 格式，不会向 LLM 抛出 Python 异常。LLM 需要检查返回值里的 `success` 字段来判断操作是否成功。

每个工具调用都会新建 `LocalMessageBus` 实例（通过 `get_db_client_fn()`），而不是复用同一个实例。这不是性能问题——`LocalMessageBus` 的 `__init__` 只接受一个已有的 backend 引用，构造成本极低，且避免了状态共享问题。

## 新人易踩的坑

工具函数名（如 `"send_message"`）就是 LLM 调用时使用的工具名，必须和 MCP 服务器注册时的名称一致。如果修改函数名，需要同时更新 `MessageBusModule.get_mcp_config()` 里注册工具时使用的名称字符串，否则 LLM 调用会报"工具不存在"。
