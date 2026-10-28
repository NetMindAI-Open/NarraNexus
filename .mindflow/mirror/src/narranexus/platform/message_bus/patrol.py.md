---
code_file: src/narranexus/platform/message_bus/patrol.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

两处函数内 `parse_db_utc` 导入改为从 `narranexus.platform.utils.run_liveness` 导入（原来经
`run_recorder` 转手），行为不变。

## 2026-08-17（三）— 板子只读一次；docstring 承认它会写库

**三次读降到两次**：`teams_due_for_patrol` 自己读一次板子，传给
`expire_stale_errands`（新增 `candidates` 参数），再用它返回的 expired id 在内存里
过滤出回收后的 items。上一版这里每个 team 每个 poll cycle 读两次
`list_active`——而这条循环是消息派发的串行前缀，团队数是会长的。

顺序仍是硬约束：`has_stalled` 与 `items[0].channel_id` 必须来自**回收之后**的板
子。在内存里滤而不是重读，因为回收本来就告诉了我们它退休了哪些 id。变异检验：把
`items` 换成回收前的 `board`，`test_the_recycle_happens_before_the_cadence_is_
judged` 必红。

**docstring 补「Not a pure query」**：这个函数现在会写库，而且那次写对**不在返回值
里**的团队也生效（没有 lead 的团队被回收了却不会出现在 `due` 里）。此前这件事只写
在函数体注释和 mirror 里，光读签名的人不会知道调它会取消工作项。

## 2026-08-17（二）— 差事回收挂在候选循环里，而不是 stalled 判定里

`expire_stale_errands` 的调用点从 `detect_stalled_items` 移到
`teams_due_for_patrol` 的循环内，**且在 `patrol_is_on` 之前**。

第一版挂错了位置：它落在整条「这个团队值不值得烧一次 LLM turn」判定链的下游，而
那条链里有两道**永久性的门**而非节流——`patrol_is_on` 要求团队已指定 lead，而
`lead_agent_id` 默认是 `None`。开项那一侧一道门都没有。于是「开」和「回收」的触发
条件不对称：**任何团队都能开，只有指定了 lead 的团队才会回收**，而从没指定 lead
的团队恰恰是最不可能有人手工清板子的那批。

`teams_with_active_work()` 是正确的作用域：它既不看 lead 也不看 `patrol_enabled`，
所以一处调用同时覆盖巡查团队和非巡查团队，也不用为一次清扫再养一个调度器。

**顺序是硬约束**：`list_active` 必须在 expire **之后**读。`has_stalled` 决定 600s
还是 180s 节奏，`items[0].channel_id` 决定巡查瞄准哪个房间——先读板子会让一条已经
不存在的行同时驱动这两件事。

`expire_stale_errands` 内部全吞异常，在新位置上更要紧：本循环的 per-team `except`
会让一次清扫失败把**这个团队整轮巡查**都跳过，用一次 stall 检测换一个记账错误不
划算。

## 2026-08-14 — stalled 按状态迁移记一行日志

`detect_stalled_items` 在写入 `status=stalled` 的**那一次**打一条 `[work-item]
action=stall`。刻意放在 if 里而不是循环里：stalled 每轮巡查都会重新推导，按轮
记会让一次断链在闭环率报告里读成几百次。

消费端是 `scripts/diag_collector/work_item_report.py`——PR #230 要求的「上更强兜
底前先测量」，量的就是这条和 [[errand]] 的 open/close 行。

# patrol.py — 让流程有人负责的那一半(平台侧)

## 为什么存在

team 房间是**纯 @ 驱动**的:每个成员(lead 也不例外)只在被 @ 时醒来。于是
一条流程能往前走,全靠每一棒都记得 @ 下一棒;任何一环忘了、静默了、或者 @ 被
级联上限剥掉,整条链就死了 —— 而且**结构上保证没有任何人会发现**。Dunhuang
断链就死在这:A3 应了声「收到」之后再无音讯。

本模块是那个「周期性激活」。放在这里的是**不能由模型说了算**的部分:判定某项
卡住,判定某个 team 到期该巡查。lead 的自由裁量从这两条之后才开始。

## running ≠ stalled,这条区分最贵

`detect_stalled_items` 认三种卡住(负责人 idle 且项未完成 / 声称 running 但
心跳死 / 压根没有 activity 行),但**心跳新鲜的 running 一律不算**,不管它跑了
多久。25 分钟的思考是**工作**;去催它就是平台成为铁律 #14 要保护的那个长任务
的打断源。

判据全部来自 `bus_agent_activity`,写回 `status=stalled` 而不是每次现算 ——
巡查提示词因此读到的是平台事实,UI 也能显示同一个词。恢复也写回:成员回来了
就退出 stalled 集合,否则会一直催一个已经在干活的人。

未认领的项不算卡住:没人接的活不存在「谁迟到了」,那需要的是派活,是另一段
提示词。

## 成本模型就是候选查询

`teams_due_for_patrol` 三道闸,按成本排序:有未完成项 → 有 lead 且没关 →
到期了。**板子空 = 零候选 = 零 run**,这是整个功能的成本保证。形状照抄
`_agents_with_pending`:一次扫全量,而不是逐个 team 去问它有没有事干。

`patrol_enabled` 为 NULL 读作**开**(对有 lead 的 team):设 lead 这个动作
本身就是在说「这个负责」。没有 lead 就不巡查 —— 平台不替用户指定负责人。

节奏自适应:常规 600s,有 stalled 项 180s(那正是流程已死而无人知晓的窗口)。

## 频控为什么必须落盘

拍板口径 (a) 让巡查消息**豁免级联深度上限**——这是它能起作用的前提:流程断掉
的现场本来就是一长串没有用户插话的 agent 消息,depth 早已到顶,不豁免的话
催办的 @ 会被无声剥掉。

代价是巡查从此不在「防 @ 风暴」的保护范围内,这个计数器成了**仅剩的兜底**。
而 bus 现有的限流活在 `MessageBusTrigger._rate_counters` —— 内存 dict +
`time.monotonic()`,workers 一重启就清零。**一重启就消失的兜底不是兜底**,所以
`patrol_spoke_at/count` 在 DB 里。

`may_patrol_speak` **fail open**:巡查的职责就是把「出事了」说出来,因为自己的
记账行读不到就闭嘴,等于在最该说话的那次把消息丢了。误放行的代价是房间里多一
行,误拦截的代价是一条流程无声死掉。

## 上下游

- **上游**:`MessageBusTrigger._dispatch_patrols`(poll cycle 的第二个候选源)
- **下游**:[[team_work_repository]](板子)、`_bus_activity` 的 `is_live`
- **相邻**:`PATROL_MSG_TYPE` 被 `_team_cascade_depth` 用来跳过巡查行

## 2026-08-10 — `patrol_is_on` 改从 `team_schema` 引入

只是引入位置变了,判定规则一字未动:那条规则读的两个字段都属于 Team,所以归
Team 的 schema 管(见 `team_schema.py.md`)。本文件仍是它唯一的 agent 侧调用方。

## 2026-08-11 — `executor_agent_id`:巡查者不是关于自己的证据

`detect_stalled_items` 判「assignee 是不是还活着」看 `bus_agent_activity`。对
**正在跑这次 sweep 的那个 agent**,这行描述的是 sweep 本身,不是它的条目:

* 在 sweep 开活动行**之前**读 → idle → 巡查者每一轮都把自己的条目标 `stalled`,
  永不恢复;`has_stalled` 把这个 team 永久钉在 180s 档;prompt 再把 `@lead`
  发进房间 —— lead 被平台安排去催自己。
* 在**之后**读 → running → 巡查者的条目永远不会 stalled。

两个读数都不携带「这个条目有没有在推进」的信息,所以不是「挑一个时机」的问题,
而是**这行对巡查者无信息**。跳过它的条目。

代价明写接受,而且比第一眼看上去大:lead 真卡在自己条目上时,**没有任何人**会
发现。(这一段此前写的是「别人的 sweep 仍然抓得到」,**那句是错的**:巡查是
**每个 team 一条、且由该 team 的 lead 执行** —— `teams_due_for_patrol` 每个
team 只产出一个 `(team_id, lead, channel)`,根本不存在第二个 sweep 可以兜底。)这是**已知
缺口**,不是被别的机制覆盖了;要补得加第二个扫描者(另一个成员,或平台侧的第二
轮),本次不做。

它仍然是两个选项里较好的那个:另一个是完全没有恢复路径的永久自我催办。而且
**陈旧的 `stalled` 会被清掉**(见下),所以缺口是「新问题没人发现」,不是「旧判决
永远挂着」。

## 2026-08-11 — 跳过巡查者 = 不下判决,不是不碰记录

`continue` 原本放在活动行查询之前,于是巡查者自己的条目连**恢复写回**
(`STALLED → IN_PROGRESS`)也一起跳过了。后果是永久的:一条**已经是** `stalled`
的条目一旦落到当前 lead 头上,再没有任何平台路径能清掉它 —— `ACTIVE` 含
`STALLED`,`has_stalled` 把这个 team 永久钉在 180s 档(巡查成本 3.3×,而其实
什么都没坏);用户面板上永久显示一个并没在发生的 stall;而且它被排除在返回的
`stalled` 列表外,prompt 里不出现,lead 永远不会被告知去 `work_update_status`
手动拨回来。

可达路径不需要构造:条目 assign 给 B → B 掉线被上一轮标 `stalled` → owner 把
lead 换成 B。此后每轮都跳过。

论证和 docstring 本来的逻辑是一致的:既然这行对巡查者无信息,那么一个**基于旧
证据的判决**就更不该继续挂着。

## 2026-08-11 — 判据要读**这个房间**的活动行

`detect_stalled_items` 一直只按 `agent_id` 取活动行,而 `bus_agent_activity` 的
主键是 **`(agent_id, channel_id)`** —— 一个 agent 属于多个 team 是设计支持的形状
(`_item_in_my_room` 的立论就建在这上面),所以它在这张表里有**多行**;而
`get_one` 是 `LIMIT 1` 且无 `ORDER BY`,MySQL 下按聚簇主键排,等于**永远取
`channel_id` 字典序靠前的那个房间**,与这条工作项在哪个房间无关。

产生的失败恰好是这个功能的立身之本被反过来:一个已经**放弃了房间 B** 的成员,
只要此刻在房间 A 跑着一轮,就被读成 live,于是 B 的条目永远不会被标 `stalled`
——Dunhuang 那条断链在多 team 场景下静默失效。而且前端 roster 是**按房间取的**,
同一时刻面板在 B 房间显示这人 idle,巡查却认为他在忙:两边对同一个人给出相反
结论,没有任何日志能解释。

铁律 #15 要求这个事实由平台从活动数据推导,那么「**读哪一条**活动数据」本身就是
判定的一部分。`WorkItem` 自带 `channel_id`,信息一直就在手边。

顺带:改完之后「负责人在本房间从未开过工」会正确落到 docstring 里第三种形状
(no activity row at all),语义反而更贴文档。

## 2026-08-11 — `_clear_stale_stall`:一个 stall 只活到证据消失为止

清除 stall 的那两行原本在同一个函数里出现两遍(一处来自「负责人回来了」的证据,
一处来自「这行对巡查者无信息」的论证)。方向不同,写入相同,原则也相同:
**stalled 是推导出来的事实,它只应该活到支持它的证据消失为止**。抽成一处之后,
「跳过巡查者」那个分支只剩它真正想表达的 `continue`。

## 2026-08-18 — patrol 轮的表达面：声明为空、两个发送动词都撤下桌、不装静音催促

patrol 提示要求 lead 把房间状态行写成**纯文本**并明确禁止 `message_team`（平台会以房间
自己的 marker 张贴，lead 调用就变成它在房间里聊天、并计入级联跳数）。但 patrol 走的是
`_invoke_runtime(team_room=True)`，于是同一轮里 `message_team` 被宣告为默认回复工具、被
两个框架的回复提醒点名 —— 和三行之上的提示直接冲突。NexusPower 上更糟：`expression_nudge`
在「本轮没调任何回复工具就收尾」时触发静音修复，而这**正是** patrol 的正确结局，催促文案
则让 lead 去做被禁止的事。

新增 `BUS_PLAIN_TEXT_TURN_EXTRA_KEY`（见 [[hook_schema.py]]）：本轮的回复就是它的纯文本。
带这个标记时 [[message_bus_module.py]] 声明为空、并把两个发送动词一起撤下桌 —— 用散文禁止
一个可见工具正是这次改造要终结的争论方式（615 次调用打在两个写着「Do NOT call」的工具上）。
`expression_nudge` 收窄为 `team_room and not patrol`。

守卫：`test_expressive_collection.py` 两条（桌面形态 + 源码钉住 nudge 收窄与 `patrol=True`
自我声明）。
