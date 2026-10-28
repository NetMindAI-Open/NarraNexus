---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/_narrative_reads.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 (review 修正) — event 路径**故意**不用 `events.user_id`

`_agent_owner_timezone` 原来的注释理由是「`narratives` 没有 user 列」——这对 narrative 路径
成立，**对 event 路径不成立**：`events` 表是有 `user_id` 的。

理由写不对防不住下一次"优化"：看到 `fetch_event_view` 为拿时区多查两次（`agents` + `users`）、
而手上 `row` 里就有 `user_id`，很自然会改成 `row.get("user_id")`——于是 `view_event` 和
`view_narrative` 可能对同一段对话报出不同时区，把本 PR 的 bug 换个地方复现。

真实理由已写进 docstring：**两个 view 必须跟时间线共用同一个 frame，而时间线按 turn owner
算**，所以两条路径都走 agent owner。

`test_both_views_agree_on_the_zone_for_one_agent` 把这条锁住了：同一个 event 分别通过
`view_narrative` 的 messages 和 `view_event` 取到，断言两个 `time` 相等。

## 2026-08-18 — `view_narrative` / `view_event` 的 `time` 改成带 frame

时间线导言明确写着「pass it to `view_event()` to fetch that turn's full detail」——这两个
面是**一条连续的路径**。时间线开始按用户时区带偏移渲染之后，这里还留着 `[:19]` 裸 UTC，
等于对同一个 event 给出两个日期，而且是平台自己把模型从一个 frame 引到另一个。

两处 `time` 现在都走 `format_timestamp_for_agent`。

**时区来源是 `agents.created_by`**：`narratives` 表没有 user 列，所以从 agent 的 owner 反查。
查不到就降级 UTC——这两个函数对外承诺永不抛异常。

**排序坑**：`narrative_chat_history` 原来按 `time` 字段字典序排。`time` 换成渲染值之后，
无法解析的时间戳渲染成 `"??"`，会排到所有真实日期**前面**，把坏行顶到历史最前。现在多带
一个私有 `_sort_ts`（原始存储值）排完即删，agent 可见的 payload 里不出现。

这两个函数同时被 `data_access/store.py` 的 DirectStore 和 `backend/routes/agents/narrative.py`
消费，改 `time` 形状等于改 agent 可见契约——前端没有读 `.time` 的地方，已确认。

# _narrative_reads.py — 共享的方言安全 narrative/event 读

## 为什么存在（PR-7）

basic_info 的 `view_narrative`/`view_event`/`switch_narrative` MCP 工具原本是**裸
MySQL**（``SELECT `trigger` … FROM events``、`SELECT 1 FROM narratives`、
`instance_narrative_links` 手写 SQL），只靠本地 sqlite 翻译垫片能跑，是双方言纪律
禁止外泄的东西。本模块把这些读**重写在 `AsyncDatabaseClient` 的 get_one/get/
get_by_ids 上**（SQLite+MySQL 通用）。

`fetch_narrative_view` / `fetch_event_view` / `check_narrative_switch` 各自返回
**完整结果 dict 且从不抛异常**——所以 AgentDataStore 的 DirectStore 与 backend
[[narrative]] 路由都能直接 `return await fetch_x(...)` 拿到**逐字相同**的输出
（parity=单一实现，不是两份手抄）。`narrative_chat_history` 从 narrative.py 提上来
共享（去重）。

## 安全：补上 agent_id 归属过滤

旧裸 SQL 按 id 查，**不校验归属**——任何 agent 传另一个 agent 的 narrative_id/
event_id 就能读到别人的内容（跨租户读）。这里的每个函数都加了
`row.get("agent_id") != agent_id → not found`（event 直接进 get_one 过滤），把读
限定在调用方自己。

## get_by_ids 的 None 契约（预审二轮 Critical）

`db.get_by_ids` **保序 + 缺失补 None**（`-> List[Optional[dict]]`）。`chat_` 实例可能
在 step_1 建 link 后、step_5 才落 memory 行——首轮被打断就永久留下「有 link 无 memory」
的实例。所以 `narrative_chat_history` 遍历 mrows 时 `if not mrow: continue` 跳过 None，
不能直接 `.get()`（否则 view_narrative 整个崩成 success:false，且是相对旧工具的回退）。
测试的 fake db 必须照真契约 `[map.get(i) for i in ids]`（补 None），不能 filter 掉——
否则测试假绿挡住这个 bug。

## truncated 覆盖两个上限（预审二轮 Important）

`truncated = 实例扇出 > _MAX_CHAT_INSTANCES` **or** `消息数 > _MAX_MESSAGES`——只报实例
上限会在实例数达标但消息超 200 时静默丢老消息且 truncated=False（违背铁律 #16）。

## 公开面（预审二轮 Important）

`fetch_*`/`check_*`/`narrative_chat_history` 经 [[basic_info_module 包]] `__init__` 的
`__all__` 导出；route 与 store 都 import **包**、不 reach 私有 `_narrative_reads`
叶子（同 social_network_module 的先例）。

## 形状（比旧工具 enrich）

统一加 `success` 键（旧 view_* 无 success、switch 用 `ok`），narrative 视图带
`truncated`（chat 实例扇出触顶时不静默丢历史，铁律 #16）。`event_log` 是**原始**
步骤轨迹串（截断 20000），不是前端 event-log 路由解析出来的 thinking/tool_calls。
