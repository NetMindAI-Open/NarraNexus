---
code_file: src/narranexus/platform/message_bus/errand.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

函数内 `parse_db_utc` 导入改为从 `narranexus.platform.utils.run_liveness` 导入（原来经
`run_recorder` 转手），行为不变。

## 2026-09-03 — 只有用户和组长的 @ 才开差事（`opens_handoffs`）

`record_handoffs` 新增 `lead_agent_id` 参数,入口先过 `opens_handoffs(from_agent, lead_agent_id)`:
发送者是 **用户**(`usr_` 前缀)或 **团队组长** 才开项;其他成员的 @ **照常唤醒对方**
(激活是 bus 的事,本模块不碰),但**不上板**。没有组长的团队,agent 的 @ 一律不开
(`patrol_is_on` 本来就要求有组长,那些行从来没人扫)。

起因:owner 复盘 dev 一个房间,19 字提问后三个成员接力 23 条,每条 @ 另外两人,
每个 @ 都成了一行 errand,渲染进全员每轮 prompt 24h,「收到」关不掉,patrol 再追问。
Dunhuang 的保证不丢:组长→A3 的差事在 A3 的承诺下仍然开着(`is_promise_only`);
A3→A4 的接力只唤醒不记账——**A4 不交付时无人追**,是明知的取舍。
`lead_agent_id` 是 **keyword-only 且无默认值**:忘传就是 TypeError,而不是一块永远不填的板子
(事故教训 #5 的形状);被门拒绝时打 `[errand] no hand-offs …` debug 日志留痕(只打 mentions 数量与前 3 个)。
调用方:[[team_posting]] 从 `teams` 行读组长;路由(用户)显式传 `lead_agent_id=None` 并注明原因。
测试:`test_errand_auto_board.py` 「Who may open one」一节 + `test_the_post_path_reads_the_lead_from_the_team_row`。


## 2026-08-17（三）— 批量上限、复合缓存键、板子可由调用方传入

**`MAX_EXPIRIES_PER_SWEEP = 100`**：过期是**爆发式**的——昨天很热闹的房间可能几百
行同时到点——而这个清扫现在跑在 bus 轮询里、排在所有 agent 的消息派发前面，每行两
次查询。超出部分下一轮（3-12s 后）继续，只是延后不会丢，并按铁律 #16 打一行说明
还剩多少。与 `MAX_HANDOFFS_PER_MESSAGE` 是**两个不同的量**：一个限单条消息开几
项，一个限单轮回收几项。

**缓存键改成 `(assignee, channel_id)`**：`bus_agent_activity` 是每
(agent_id, channel_id) 一行，而 `get_one` 是没有 ORDER BY 的 LIMIT 1——只按 agent
缓存会让一个房间的活跃度裁决另一个房间的差事，正是 [[patrol]] 里那段注释记录过的
事故形状。今天一个 team 只有一个房间所以不可达，但没有任何地方强制这一点
（`create_item` 接受任意 channel_id），而这里判错的方向是**不保守**的那一侧：本该
留下的行被 `cancelled`，报告记成「过期」而不是「交付」。

**`candidates` 参数**：让调用方把已经读到的板子传进来。见 [[patrol]]——那个循环每
个 poll cycle 跑一次而不是每次巡查跑一次，重复读一次板子就成了消息派发串行前缀上
的常驻开销。

## 2026-08-17（二）— TTL 量的是「没人管了多久」，不是「开了多久」

过期判定加一条：assignee 当前 `is_live` 的项本轮跳过。

只看年龄会和铁律 #14 撞车——跑几十小时的交接是合法形状。一条跑到第 30 小时的交
接，第 24 小时行被置 `cancelled`，第 30 小时真交付时 `list_open_errands` 已经查不
到 open 项，于是**不产生 `action=close` 日志**，闭环率报告把一次真实交接记成「过
期」。同文件的邻居 `detect_stalled_items` 早就用同一份证据（`bus_agent_activity`）
避免这个误判，理由一模一样（「25 分钟的思考是工作」）。

按 **assignee** 而不是按 item 查活动行：一个房间就几个人却可能有很多行，按 item
查会把一次清扫变成 N 次读。

活动行读不到时按 **live** 处理（即不过期）：「未知」不是「空闲」，把 DB 抖动当空
闲会往不可逆的方向误判。

## 2026-08-17（二）— 回收器的调用点移走

见 [[patrol]]：`expire_stale_errands` 现在由 `teams_due_for_patrol` 在
`patrol_is_on` 之前调用，本模块的函数签名与语义不变。

## 2026-08-17 — review 三条：上限、回收、一次交付结一件

narranexus-review 抓到的都是**「自动开项安全」这个论证只覆盖了会闭的那些**。

**`MAX_HANDOFFS_PER_MESSAGE = 5`**：`targets` 直接来自模型写的 @mention，此前
无上限。每一行都进每个成员每一轮的 prompt。它同时是**延迟预算**——投递搬进 turn
内之后（#291/#302），这段记账跑在 runtime 等回调的那段时间里，每个目标两次索引
查询。超出部分**写日志说明丢了谁**，不静默截断。

**`ERRAND_TTL_HOURS = 24` + `expire_stale_errands`**：一条永远不会被交付的差事
此前是**永久**的，而且不是躺着——`stalled` 属于 ACTIVE，所以一行卡住就让
`teams_with_active_work()` 永远返回这个团队、把巡查永久钉在 180s、每 30 分钟烧
掉 6 次真实 turn。[[patrol]] 把「空板子 = 零 run」写成这个功能的成本保证，而一
句修辞性的「@Bruno 你怎么看」就足以让整个团队退出那条保证。

过期一律置 `cancelled` 而非 `done`：`done` 会被闭环率报告记成交付，等于把「没人
管到过期」美化成「交付了」，而那份报告是这批度量工作的全部价值。24h 而不是更短
——天花板必须容得下一次合法的长交接（铁律 #14 保护跑几十小时的 run）。

**一次交付只结一件（最早的那件）**：此前无差别关掉该 agent 在该房间的全部
open。六段流水线里同时欠三件是常态，交付一件就把三件都记成 done，另外两件从板
子上消失、巡查不再追、面板显示一切正常。这就是「被误判为交付」的另一种形态，与
`is_promise_only` 的偏向同一条论证。它还会系统性高估闭环率（一次投递三行
close）。取最早：既最可能是这次交付的指向，也最接近该被催的那条。

## TTL 为什么在 Python 侧算年龄

第一版把 `created_at < %s` 写进 SQL，**SQLite 上静默匹配不到任何行**。同一列存
着两种文本形态：schema 默认写的是 `2026-08-17 02:52:40`，Python 写的是
`2026-08-17T02:52:40+00:00`，字符串比较在分隔符处就错了（'T' > ' '），而 SQLite
是桌面版的生产后端。改为复用 [[team_work_repository]] 已有的 `list_active` +
`parse_db_utc`，与 `patrol_due_at` 同一套做法，**且不新增裸 SQL**。
`test_expiry_survives_both_timestamp_shapes_in_the_column` 把两种形态都种进去。

# errand.py — 让交接自己上板子

## 为什么存在

工作板（#259）已经能发现一个任务卡住、收紧巡查节奏、催办负责人。它缺的是**入
口**：唯一的开项方式是 `work_add_item`，一个 Leader 必须记得调的工具。于是整
条防线压在模型服从上——正是铁律 #15 要从正确性关键位上摘掉的那种依赖——而创始
案例正好从这个口子穿过去：敦煌断链那一轮**没有人调过那个工具**，板子是空的，
巡查无事可扫。

本模块是 owner 在 2026-08-07 拍板的那一层：**差事是消息级事实，自动记录**
（A @ B 即开，B 回帖即闭），与工具维护的**任务级**工作项分层不合并。两者同住
`team_work_items`，靠 `origin` 分辨。

## 为什么开和闭必须一起写

只开不闭**严格劣于什么都不做**。群里一句「@Bruno 你怎么看」也会开项，而板子随
prompt 注入给每个成员每一轮，巡查还会一直催到有人手工清理。入口的价值取决于
出口存在，所以两半在同一个文件里写、一起改、一起测。

## 承诺不算交付——这条规则是整个模块的重心

`is_promise_only` 的偏向是**故意不对称**的：误判为「承诺」的代价是多一行催办
（还被 `may_patrol_speak` 限了 6 次/30 分钟）；误判为「交付」的代价是**在这个
机制唯一为之而生的那条消息上**整个失效。「收到，开始处理……完成后交付 @A4」是
那次 run 的全部 final_output，把它读成交付，就等于让板子附和 runtime 说「活干
完了」。

分类器是启发式的，**并且允许是**，因为它后面挂不了任何不可逆的动作：项保持
open 只意味着巡查问一句，永远不会停任何 run（铁律 #14）。

## 为什么不上 LLM 分类器

两个理由，任一都足够：把正确性关键判断重新压回模型服从（铁律 #15）；给每条团
队消息的投递路径加一次模型调用。词表小到一屏能读完是设计目标，不是妥协。

## 调用顺序：先闭后开

`message_bus_trigger._record_errands` 固定 close → open。在那条创始消息上，close
是空操作（是承诺不是交付），open 补上下一棒——于是**两跳都还被盯着**，这正是断
链需要的。反过来会让一次交接把自己刚创建的差事关掉。

## Gotcha

- 只在**投递落地之后**调用。给一条房间从没见过的消息开差事，会让板子和聊天记
  录互相矛盾，而板子是巡查信任的那个。
- 全程 best-effort：回帖已经进房间了，记账失败绝不能反过来把成功的一跳弄失败。
- 闭环按 **channel** 而不是 team 收敛：一个 agent 属于多个团队，在这屋说话不能
  结清那屋欠的账。
- 只碰 `origin=auto`。`tool` 行是任务，一个任务跨多次差事，自动关掉会把 owner
  分的那两层重新压平。

## 上下游

上游：[[message_bus_trigger]] 的团队回帖分支。
下游：[[team_work_repository]]（`has_errand_for` / `list_open_errands`）、
[[patrol]]（读同一张板子判 stalled）。
`[work-item]` 日志行被 `scripts/diag_collector/work_item_report.py` 读取。
需求出处：飞书 PRD《异步协作：让「我干完再回来找你」成为可兑现的承诺》第二层。
