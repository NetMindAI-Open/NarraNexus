---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/_basic_info_mcp_tools.py
last_verified: 2026-09-09
stub: false
---


## 2026-09-09 — submit_feedback 加 (c) 平台注入凭据被拒触发 + 工具侧去重闸门

与 [[prompts.py]] Product Feedback Duty 第 3 条同口径：**平台注入的**凭据 / 端点 /
额度被平台工具拒绝时 `category=error` 上报，写明工具名与错误码；明确排除没绑定时的
`no_credential`、`official-agent-required` 类策略拒绝、用户刚输入的 secret 被拒；
同一次失败同时命中 (b)/(c) 时只按 (c) 记一条。

**为什么「一次」必须落在代码里**：触发 (a)/(b) 天然被人交互频率限住（用户得先抱怨 /
同指令得先失败两次），(c) 是机器生成的——平台侧故障在**每一次工具调用**上复现，且是
平台级而非单 agent 级。若只靠文案，一次 token 送错后端的事故就会给
`feedback_client` 灌几百上千条同义 `category=error`，团队反而看不见信号；而长
`agent_loop`（铁律 #14）上下文被压缩后，「我已经报过这个码了」是最先丢的事实，工具又
恒返回 ok=True，等于每次都在强化再报一次。

实现：可选参数 `dedup_key`（**必须可选**，`= ""`；改成必填会直接打断触发 (a)/(b)
的既有调用）。`_dedup_reserve` 在**发送前**占位（而不是发送后记录），关掉两个并发调用
同时通过的窗口；命中已占位则跳过 POST 并返回 `ok=True`——不能返回失败，工具契约要求
agent 不重试、不向用户道歉（见本文件 docstring 与 [[feedback_client.py]] 的
fire-and-forget 约定）。`_dedup_release` 在发送没落地时撤销占位：否则缓存里存的就是**失败哨兵**，接收端一挂
就把这个 agent+错误码唯一的一次上报名额白白吃掉，事故永远报不出去；撤销后失败路径与
去重前的老行为一致。

**结算必须在 `finally` 里**（Opus 预审 C1，第三轮；前两轮都没看这条出口）：
`send_feedback` 只 catch `Exception`，而 3.8 起 `asyncio.CancelledError` 不是
`Exception`。MCP 调用在发送中途被取消（或任何东西从里面抛出去），占位就会以
`[stamp, False]` 挂满整个 6h TTL——之后每一次调用都被压掉、一条 POST 都没发出去，还被
告知「已经有一条在路上」。事故最可能被取消的时刻恰恰是接收端也在挣扎的时刻，于是本该
上报的故障变成永远报不出去，而且日志里看着像功能正常。规则写死：**每一条出口都要结算
占位**。`logger.info` 留在 `try` 外——被取消的请求根本没有结果，记 `delivered=False`
等于编造一个。

**占位有三态，不是两态**（Opus 预审 C1 打回的正是这里——第二轮修复在自己的边界上又开
了一个口子，与 PR#260 连打五轮同一形状）。记录是 `[stamp, delivered]`：
- `DEDUP_NEW` —— 本次调用拿到坑位，负责真正发送。
- `DEDUP_PENDING` —— 别人正在发、结果未知。**必须回 `notified=False`**（「已经有一
  条在路上，结果还不知道」），绝不能说「团队已收到」。只判断「在不在」的两态实现会在
  这里撒谎：`send_feedback` 有 3s 超时且吞异常，占位未决窗口最宽的时刻恰恰是接收端也
  在挣扎的时刻；一个 agent 同时服务多个会话、平台级故障让它们在同几秒内全部命中同一
  个 `narra_cli:agent-token-invalid`，于是除第一个之外每个会话都会对用户说「团队已被
  通知」，而唯一真跑过的那次发送失败了——正是 `67719fcc1` 要消灭的那句假话，反过来
  又长了回来。
- `DEDUP_CONFIRMED` —— 确实投递成功过，这时命中才允许说「已通知」。
成功后由 `_dedup_confirm` **原地**把 `delivered` 置 True（改 value 不动 key，插入序
== 时间序的前向清扫契约才不被破坏；千万不要 pop 再插）。`_dedup_confirm` /
`_dedup_release` 都带**身份校验**（`is record`）：坑位被容量/TTL 淘汰后可能已被别人重
新占用，只按 key 删会把别人正在用的占位删掉。

`feedback_disabled()` 与空 `summary` 都在占位之前就短路（各自返回
`FEEDBACK_DISABLED` / `FEEDBACK_NO_SUMMARY`，不动缓存）——否则「本部署根本不上报」和
「压根没写 summary」都会被 `send_feedback` 的 `False` 说成「刚才没联系上」，还白白吃掉
一个去重名额。空 summary 是唯一一个 agent 真能自己解决的 outcome，所以也是唯一一个让它
再调一次的。`category` 也在入口
统一归一化（未知值 → `other`），否则 [[feedback_client.py]] 按 `other` 投递、
`_feedback_result` 却按原字符串描述，同一条上报两半各说各话。

**返回值承载「能对用户说什么」**（Owner 定调 2026-09-09）：`_feedback_result` 按
`category` + 真实投递结果构造消息，取代原来那句恒定的「Feedback recorded」。
- `delivered=False`（发送跑了但没落地）→ `notified=False` + 明确禁止告诉用户
  「已通知团队」。这是本次真正的修复点：
  [[prompts.py]] 原文案让 agent 无条件宣称团队已被通知，而 [[feedback_client.py]]
  是 fire-and-forget、异常只进 DEBUG 日志，agent 根本无从知道有没有发出去。
- `delivered=True` 且 `category="error"` → `notified=True`，允许转述「团队已被通知」，
  但仍禁止断言原因。只有这一类会覆盖「默认不向用户提 telemetry」：用户正卡在平台侧
  故障上，「有人已经知道了」是我们唯一能给的真话。
- 其余 category → `notified=True` 但消息不提通知，维持默认不宣告。
- 去重命中且状态为 `DEDUP_CONFIRMED` → `notified=True` +「Already reported」：只有
  投递成功过的占位才会带 `delivered=True`，所以这时命中确实证明团队收到过。命中
  `DEDUP_PENDING` 则 `notified=False`（见上）。
恒为 `ok=True` 不变（工具契约：不重试、不道歉）。

边界：`agent_id` 与 `dedup_key` **两半都是**调用方（模型）可控文本，故**两半都**截断到
`FEEDBACK_DEDUP_KEY_MAXLEN`（只截 key 的话仍是 4096 条 × 无界字符串）；字典按
`FEEDBACK_DEDUP_MAX_ENTRIES` 上限 + `FEEDBACK_DEDUP_TTL_SECONDS` 过期从头清扫
（stamp 只写一次不刷新，所以插入序即时间序）。
**单进程假设**（铁律 #20）：basic_info MCP server 是一个进程、全 deployment 共用，
本闸门**不跨进程、不跨重启**，也不替代接收端幂等——真正彻底的做法是把 dedup_key 带进
intake payload，但 intake 在本仓外、要先和团队约定字段。

## 2026-08-10 (PR-7) — view_narrative/view_event/switch_narrative 迁走 seam

三个读工具改为 `get_agent_data_store().view_narrative/view_event/switch_narrative`，
数据访问下沉到 [[_narrative_reads]]（方言安全，DirectStore/backend 路由共用）。
本文件的**裸 MySQL 全删**（`SELECT \`trigger\``、`SELECT 1 FROM narratives`、
`_narrative_chat_history` 的手写 SQL），连同 `_parse_info`/`_narrative_chat_history`
两个 helper 及 `get_db_client`/`json`/`Any,Dict,List` import 一并清掉（死代码）。
**行为变化**：结果加 `success` 键（switch 旧 `ok`→`success`）、narrative 带
`truncated`、且**按 agent 归属过滤**（旧裸 SQL 能读别人的 narrative/event，跨租户）。
`create_narrative` 是纯信号（无 db），不迁。

## 2026-07-10 — submit_feedback 工具（Feedback 机制一期）

新增 `_register_feedback_tool`：`submit_feedback(agent_id, user_id, category,
summary, severity)`，Agent 觉察到用户不满、或同一指令连续失败 ≥2 次时调用。
经 [[feedback_client.py]] fire-and-forget 发到团队反馈接收端（写死 URL，
`NARRANEXUS_FEEDBACK_DISABLED=1` 可关）。隐私契约在 client 层强制：id 全部
哈希、只送 Agent 自己写的一句话摘要。工具恒返 ok=True——投递失败不该让
Agent 重试或向用户道歉。(设计记录为作者本地,不入库)


# _basic_info_mcp_tools.py — narrative-awareness MCP tools (Fix #2 P3)

## Why it exists

Gives the agent visibility + agency over the unified chat timeline built by
[[chat_module.py]] (`gather`). The system pre-picks a narrative for
each turn, but that pick is imperfect (esp. for short replies). These four tools
let the agent inspect threads/events and correct the routing.

## 上下游关系
- **被谁用**: the agent (LLM), via [[basic_info_module.py]]'s MCP server
  (`create_mcp_server` → `create_basic_info_mcp_server(port=7808)`).
- **依赖谁**: the read/validate data access now goes through the AgentDataStore
  seam (`get_agent_data_store()` → [[_narrative_reads]]); this file no longer
  imports `get_db_client` or writes raw SQL. `create_narrative` needs no db.

## 设计决策

**Two read tools, two signal tools.**
- `view_narrative(narrative_id)` / `view_event(event_id)` are reads that now
  route through the seam (DirectStore local / HttpStore cloud, via
  [[_narrative_reads]]); they return full thread history / full event detail
  (the timeline only shows a trimmed slice + the sent message). PR-7 replaced
  the old raw MySQL with dialect-safe, agent-scoped reads.
- `switch_narrative(narrative_id)` also routes through the seam (an
  existence+ownership validation). `create_narrative(title, description)` is a
  SIGNAL — validate/echo and return, no db, no migration. The MCP tool process and the agent_runtime are different
  processes; the runtime detects the tool CALL (args) in
  `_detect_narrative_routing_signal` and does the re-attribution in
  [[step_4_persist_results.py]] 4.0. `create_narrative` deliberately does NOT
  create in the tool (avoids double-create) — the runtime creates it from the
  {title, description} args. Keep the tool names in lockstep with step_4's
  detector (`SWITCH_NARRATIVE_TOOL` / `CREATE_NARRATIVE_TOOL`).

## Gotcha / 边界情况

- Tools take `agent_id` (+ `user_id` for create) as params, following the
  artifact-tool convention — the agent fills them from its instructions.
- `view_event` reads `events.event_log` (may be bytes) and truncates large
  fields to keep the tool result bounded.

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
