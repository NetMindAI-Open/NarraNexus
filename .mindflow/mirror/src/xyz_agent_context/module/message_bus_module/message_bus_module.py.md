---
code_file: src/xyz_agent_context/module/message_bus_module/message_bus_module.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — bus 轮次的回复面声明（origin-aware）+「干完活必须交付」纪律

P0 recvrdLPavENwg（8/1 briefing squad：5 个分析师真研究、纯文本收尾、零交付）
的声明侧修复。新增 `get_expressive_tools(ctx_data)` 覆写：**只在**
working_source=MESSAGE_BUS 的轮次声明 `bus_send_message` + `bus_send_to_agent`
（fully-qualified，派生自 get_mcp_config().server_name）。三重门：
① 非 bus 轮不声明（chat 轮广告 bus 工具会诱导经 bus 回 owner）；
② team 房（extra_data `bus_team_room`，由 [[message_bus_trigger]] 盖章）不声明——
纯文本自动上墙、prompt 禁投递工具，声明会诱导双发；③ 无 ctx 不声明。
配套 `owns_working_source`：收集点（[[context_runtime]]）把来源模块的声明排
到最前，默认回复工具从此跟着「谁联系的你」走。

Reply Discipline 同批加一条「**Finished work is never ping-pong — deliver it**」：
沉默许可只给"没实质内容"，做完别人求的活必须用 bus 工具送达，纯文本收尾
= 零交付。注意：与 2026-08-01 那条同理，文案对弱模型效力有限，真正的机制
修复是声明面（本条）+ 判定面（message_bus/__init__）对齐。

## 2026-08-01 — 指令新增「替 owner 去问另一个 agent」剧本

P1 段 06:owner 说"问问教学专家在干嘛",agent 答做不了。能力一直都有
(`bus_send_to_agent` 会触发对方),缺的是**把这类请求认出来并给出路线**。
新增小节明确:① 这类请求你能做,**不得回答无法联系其他 agent**;
② 从 Known Agents 取准确 id;③ 用 `bus_send_to_agent` 发问,
**别用社交网络/联系方式工具**(那返回联系方式,不是答案);
④ 告诉 owner 已问、回复会另开一轮;⑤ 对方回复到达时用
`send_message_to_user_directly` **回报给 owner**——并写明
Reply Discipline 只管对**同伴**的回复,绝不压制对 owner 的回报
(不写这句,那条"没实质就沉默"的规则会把用户要的答案吞掉)。
找不到目标要问清楚,那是澄清问题、不是拒绝。

文案进 `_static_instruction_parts`(静态、逐字稳定,可缓存),有测试断言
稳定性与各条要点。

Reply Discipline 同批加了一条「问题从来不是 ping-pong,必须回答」——含
「替 owner 转达的问题」和「回报自己 owner 不算交差」。**但要知道:光加这
条文案对被测模型无效**(真机 3/3 仍拒答),真正起作用的是
[[message_bus_trigger]] 那侧把假的 Owner Relay 指令换掉。这条文案保留是
因为它本身正确、且对强模型有用,**不要**把它当成该问题的修复。

## 2026-07-28 — R4b：三个数据列表搬进 get_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`get_instructions` 原本 = 使用规则 + Known Agents / Your Channels / Unread
Messages 三个列表；unread 每轮消费必变、另两个被 bus 工具会话中途改变
（prod 稳定性 11/17）。现拆为：

- `_static_instruction_parts()` — 使用规则（仅烘焙 self.agent_id，会话内恒定）。
- `_volatile_context_parts(ctx_data)` — 三个列表，渲染逻辑（MAX_* 上限、顺序、
  文案）零改动。
- `get_instructions` — flag 开 → 只拼 static（轮间字节稳定）；关 → static +
  volatile 同块拼接（legacy 逐字节一致）。
- `get_turn_context` — `### MessageBus — Current State` 稳定标题 + 三个列表；
  三个列表全空 → ""。

"unread messages are already injected into your context automatically"（规则
段 :182 附近）的表述依然成立——注入位置变了，行为没变。

第 4 步 "Fetch channels" 的查询原本 `ORDER BY c.updated_at DESC`，但
`bus_channels` 表从来没有 `updated_at` 列（schema_registry 里只有
`channel_id/name/channel_type/created_by/created_at`，且 local_bus 建频道时
也只写 `created_at`，无任何代码维护 `updated_at`）。SQLite 抛
`no such column: c.updated_at`，被 `except` 吞成 `logger.debug`，结果
`bus_channels` 上下文静默缺失。改为 `ORDER BY c.created_at DESC`（表中唯一
存在的时间列）。同类事故的旁证见 [[message_bus_trigger.py]] :497 的注释。

## 2026-05-19 — Reply Discipline 段强化 Agent-to-Agent 简洁优先

新增两条规则：
1. 显式标注"对方是 agent 不是 human"，要 Agent 跳过寒暄、首选一句话 /
   单数字 / 单 list 这种最小回复形态。
2. "Substance-empty → 明确选静默" — 没新信息时不要 call `bus_send_*`，
   直接结束这轮；平台按 `[NO_REPLY]` 处理，unread 游标按正常方式推进。

跟 [[prompts.py]] (chat_module) 配对：chat 路径强调"对人要温暖"，bus
路径强调"对 agent 要极简"。两边各自收紧自己的边界。

# message_bus_module.py — MessageBus Module 主体

## 为什么存在

`MessageBusModule` 是 `XYZBaseModule` 的子类，遵循 Module 热插拔协议。它负责两件事：在每次 AgentRuntime 执行前（`hook_data_gathering()`）把 MessageBus 的状态（未读消息、频道列表、已知 Agent）注入上下文；在 MCP 服务器里暴露 MessageBus 操作工具供 LLM 调用。

如果没有这个 Module，Agent 就对 MessageBus 的存在毫无感知——不知道有新消息，也不能主动发消息或管理频道。

## 上下游关系

**被谁加载**：ModuleService 根据 `MODULE_MAP` 在 AgentRuntime 初始化时按需加载；MCP 服务器通过 `module_runner.py` 启动时实例化。

**调用谁**：实例化一个 `LocalMessageBus`（通过 `get_db_client()` 取 backend）；调用 `_message_bus_mcp_tools.py` 里的工具函数暴露 MCP 工具；在 `hook_data_gathering()` 里调用 `bus.get_unread()`、`bus.get_channel_members()` 等取数据。

## 设计决策

Instance 级别是 **Agent-level**（`is_public=True`），即每个 Agent 有一个全局共享的 MessageBusModule 实例，不是每个 Narrative 各自一个。这是因为 MessageBus 是 Agent 级别的通信能力，不需要按 Narrative 隔离。

`hook_data_gathering()` 中注入的消息格式以 `[MessageBus · {from_agent}]` 开头（类似 Matrix 的 `[Matrix · ...]` 前缀），让 continuity.py 的 `_extract_core_content()` 能识别并提取核心内容。如果这个前缀格式改变，需要同步更新 `continuity.py` 的处理逻辑。

在 `WorkingSource.MESSAGE_BUS` 触发路径下，`hook_data_gathering()` 注入的信息会更精简（可能不注入 "已知 Agent" 等非关键列表），以减少 token 消耗——因为此时 LLM 的主要任务是回复特定消息，不需要完整的 bus 状态概览。

## Gotcha / 边界情况

`MESSAGE_BUS_MCP_PORT = 7820` 是该 Module 的 MCP 服务器端口，如果其他 Module 使用了这个端口会发生冲突。新增 Module 时注意检查端口占用。

Module 实例是 Agent-level 的，但 `hook_data_gathering()` 运行时的 `agent_id` 来自 `ctx_data.agent_id`——同一个 Module 实例可能为不同的请求提供服务，不要在实例变量里缓存 agent_id 相关的状态。

## 新人易踩的坑

`MessageBusTrigger`（外部驱动 Agent 处理消息）和 `MessageBusModule.hook_data_gathering()`（Agent 主动查询 bus 状态）是两个独立的机制，可以同时工作。不要误以为开启了 Module 就不需要跑 `MessageBusTrigger`——前者是"Agent 主动感知 bus"，后者是"bus 主动推送消息给 Agent"。
