---
code_file: plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/_work_board_mcp_tools.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-17 — 工作板并入 team 家族命名

`team_work_add` / `team_work_list` / `team_work_claim` / `team_work_complete` /
`team_work_update_status` → `team_work_add` / `_list` / `_claim` / `_complete` /
`_update_status`（决策 ⑦）。

理由是它们的作用域**就是** team 房间，而此前同一台 MCP server 上有两套命名家族
（`bus_*_team_*` 与 `work_*`）指着同一个作用域——agent 得学会「team 的事有两种拼法」。
现在只有一种。

trigger 的 team prompt 与 [[patrol]] 里点名这些工具的地方同批更新；漏一处就是 prompt 指着
一个不存在的工具名。

## 2026-08-17 — 第 1 分支改用共享 helper

`_resolve_team_room` 的注入身份分支换成 [[team_rooms]] 的 `primary_room_of`。

第 2 分支（`bus_agent_activity` 兜底）**留在本文件**：它回答的是「没有注入身份时
怎么办」，和「团队房在哪」是两个问题。helper 只承载后者。

# _work_board_mcp_tools.py — 工作板工具,以及模型能写什么的边界

## 为什么与 _message_bus_mcp_tools 分文件、又挂同一个 MCP server

**挂同一个 server**:工作项的作用域是 team **房间**,而房间就是一个 bus
channel —— 能在房间里说话的 agent,恰好就是该维护这块板子的 agent。做成独立
Module 要新端口、新 instance 生命周期,还得反过来去查 bus 的表(跨 module
依赖,铁律 #3)。

**分文件**:板子有自己的状态机和自己的「平台/模型」写入边界,混进 500 行的
消息工具里会把这条边界埋掉。

## 这条边界就是本文件的重点

模型可以 open / claim / finish。模型**不能**写:

- `stalled` —— 平台从 `bus_agent_activity` + 差事超时推导(铁律 #15)
- `paused` —— 停止留下的。**能暂停自己板子的 agent 就能关掉巡查**,而巡查正是
  这个功能要加的监督
- `cancelled` —— 用户的决定,不是 agent 的

`team_work_update_status` 用 `MODEL_SETTABLE` 白名单挡住,并在错误里说明**为什么**
不能写,而不是只说不行。

## `_resolve_team_room` 为什么查 activity 而不是读注入头

`bus_agent_activity` 的镜像行**只有 trigger 的 team 分支会写**,所以「在
team-owned channel 里有一条 running 活动行」恰好就是「存在工作板」的充要条件。
peer DM 或 owner 私聊解析成 `(None, None)`,工具带理由拒绝 —— 好过凭空造一个
team,写下一个永远不会有人看到的工作项。

代价:这依赖 activity 行的写入时机。若将来 team 分支不再写它,这里会静默退化
成「没有板子」(而不是写错板子)——方向是安全的。

## 2026-08-10 — 本文件有**两条**边界,不是一条

这份文档原来把「平台/模型边界」等同于 `MODEL_SETTABLE` 白名单(哪些状态模型不
准写)。那只是第一条。第二条是 `_item_in_my_room`,挂在三个写工具上,管的是
**哪些条目模型不准写**。

`item_id` 全局唯一,所以一个只收 id 的工具天然可以跨 team 写。这条路**不需要
攻击者**:一个 agent 可以同时属于多个 team,而 prompt 的工作板段落对每个条目都
印了 `id=`,于是上一轮 team A 的板子就躺在上下文里,这一轮跑在 team B。

不匹配时**一律报 `not found`,不能报「存在但不属于你」** —— 后者会把另一个
team 的 id 存在性回泄进同一个上下文,而那个上下文正是 id 的来源。

守卫**接收 repo 而不是自己 new 一个**:工具本来就有 `get_repo_fn` 注入缝,守卫
另开一条连接的话,它读真表、它守的工具读注入表,两边会对「这个条目存不存在」
给出不同答案。

## 2026-08-10 — 房间解析:注入身份第一,activity 行退居兜底

原来只认 `bus_agent_activity` 的 running 行,理由是「那行只由 trigger 的 team
分支写,所以『在 team 房间有活跃行』恰好等价于『有板子』」。这个等价在**第二条
lane 开始在 team 房间跑 agent** 的那一刻失效:巡查在消息派发之外唤醒 lead、不写
这张表,于是 5 个工具在平台自己叫 lead 调 `work_complete_item` 的那轮全部失败。

本文档原先就写下过这个风险 ——「代价:这依赖 activity 行的写入时机。若将来 team
分支不再写它,这里会静默退化成『没有板子』」。巡查 lane 就是那个分支,隐患已经
兑现,所以现在:

1. **`caller_team_id_from_request()`** —— 这一轮能**证明**的东西,平台盖进 MCP
   头,模型无法伪造。房间 channel 由 team 推出(team 房间就是 `created_by` 为
   `team_<id>` 的那个 group channel,确定性的)。
2. **activity 行** —— 没有注入身份时的兜底。

两者冲突时**身份赢**:activity 行说的是 agent 最后被看见的地方,可能是它已经
离开的房间,往那里写条目就是一次带着合理外观的跨房间写入。

注入了 team 但房间还不存在时返回 `(None, None)` 而不是 `(team, "")` —— 每个写
入都要 channel_id,半个答案会把条目落进空字符串。

## 2026-08-11 — `TEAM_ROOM_OWNER_PREFIX` 改为 import

本文件此前自定义了同一个字面量。定义现在唯一地住在 [[team_schema]]。

## 2026-08-11 (review 收口 3) — 删掉悬空注释

同上：常量改为 import 后，那句「同 teams.py 和 trigger 的房间前缀约定」下面已无定义，
且指向的两个模块都不再拥有它。

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
