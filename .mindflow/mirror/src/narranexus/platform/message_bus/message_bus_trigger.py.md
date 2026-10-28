---
code_file: src/narranexus/platform/message_bus/message_bus_trigger.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 review）— roster capabilities 编码、marker 单一来源、无 token 退路

见下方 2026-09-11 段的不变量表（已按本轮改动原地更新）：`· can:` 的每个 capability 是标签；附件 marker
改由 `attachment_schema.file_marker` 统一渲染，marker 内每个值（含 path/mime/kind/发送者）都是字面量；没有唯一 @token 的成员给出
`@all` / `message_agent` 退路。编码器迁到 `platform.utils.inline_field`。

## 2026-09-11（PR#401 同类扫描）— 团队房 prompt 的行语法按构造不可伪造

**不变量**：团队房 prompt（`_build_team_prompt` 及其子块）与 peer 私聊 prompt（`_build_prompt`）
里，任何作者可写的文本都不能开出一行该块语法里的新行、也不能伪造同一行里的字段。两种手段，均来自
平台层 [[../utils/inline_field]]（插件与 attachment schema 同样从这里 import，平台不 import 插件）：

- **标签**（名字、描述、标题）→ `inline_field`：JSON 字符串字面量，折叠空白、替换反引号、超长带
  `…` 截断。**句柄**（agent/team/item id）→ `inline_field(x, None)`：不截断、不加引号。
- **正文**（消息、公告规则，可合法多行）→ `body_lines`：首行接在行头后，后续行一律以
  `BODY_LINE_PREFIX`（`"  > "`）引用，任何块的行都不以它开头；无独立行头的整段 → `quoted_block`
  （每行都引用）。

**不变量的覆盖范围**（逐块穷举）：

| 块 | 行语法 | 作者可写部分的处理 |
|---|---|---|
| `You are "<me>"` | 单行 | 名字 → 标签 |
| roster `_roster_lines` | `` - `id` — "name" @token (you) · Leader: "desc" · can: "cap", … · status `` | 名字/描述 → 标签（描述上限 `INLINE_DESCRIPTION_MAX_CHARS`=80，与 Known Agents 同口径）；id → 句柄；`@token` 见下；**capabilities → 每个 token 一个标签**（来自 `collect_agent_capabilities`：模块类 token 安全，但 marketplace `skill_id` 取自发布者 manifest 未校验）；`(you)` / `· Leader` / `+N more` / status（`_member_status`：`running`/`running (Ns)`/`running but no signal`）平台构造 |
| 团队卡 `[Team] "<name>"` | 单行 | 名字 → 标签 |
| 公告栏 `_render_bulletin` | `N.[ (added by "<name>")] <body>` | 署名移到**首行、正文之前**（正文无法仿造）；署名名字 → 标签；规则正文 → `body_lines`，换行不能再造一条「无署名 = owner 写的」规则 |
| 公告栏 `[Team progress]` 摘要 | 表头下整段 | 平台 LLM 由房间内容生成 → `quoted_block` 整段引用，不能开出 `[Work board]` 等表头或编号规则 |
| 工作板 | `- [status] "title" ("who") · id=<item_id>` | 标题/负责人 → 标签；id → 句柄；`status` 走 `WorkItemStatus.MODEL_SETTABLE` 白名单，非自由文本 |
| 团队共享目录 / `message_team(team_id=…)` 提示 | 单行 | 目录由 `team_shared_dir(owner, team_id)` 平台拼成；`team_id` 为系统句柄 |
| 巡查停滞列表 | `- "title" ("assignee")` | 标签 |
| scrollback | `<sender>[ [→ names]]: <body>` | 发送者、被点名者 → 标签（`User`/`[system]` 为平台常量不加引号）；`[→ …]` 移到**冒号前**（正文够不着）；正文 → `body_lines`。`[system]` 行正文同样 `body_lines` |
| 附件 marker `build_bus_markers` → `attachment_schema.file_marker` | `[Shared file: name="…", path="…", mime="…", kind="…", from="…"[, transcript="…"] — use Read tool to view]` 一行 | **每个值**都编码：`path` 用无损的 `exact_literal`（逐字节回解），文件名、mime、kind、发送者、transcript 用 `inline_literal`；只有 `Shared file`、键名与结尾是固定字面量。发送者传原始值（团队房 `_sender_raw`，DM 传句柄）由 `file_marker` 编码；`from=` 一律是加引号的标签，DM 里 agent 从裸 `From:` 行抄句柄。用户上传 marker 走同一函数 |
| 指向行 `You were just @mentioned by <who>` / 批次列表 `- <who>[ [no @mention — routed to you]]: <body>` | 同 scrollback | `_who` → `_sender`（标签）；平台触发的 `_platform_trigger_label` 是平台常量，不加引号；routed 标记移到冒号前；正文 → `body_lines` |
| peer 私聊 `_build_prompt` | `From: <id>` / `Time: <ts>` / 正文 | `From` 为系统句柄、`Time` 为平台时间戳；正文 → `quoted_block`，不能伪造另一条消息的 `From:`/`Time:` 头；附件 marker 同上 |

**@mention token**：roster 显示的是带引号的名字，而 `extract_team_mentions` 只认裸 `@word`，模型照抄会
写出 `@"Ana"` → 静默不唤醒。所以每行在名字后显示 `@<token>`（`team_posting.mention_token`，用真解析器
回验该 token 只唤醒此成员），名字没有唯一 token（以非 token 字符开头、或与他人同前缀）时显示
`(no @mention token — @all reaches everyone; message_agent with the id above reaches them alone)`——给出真实存在的退路
（`message_team` 没有按 id 点名的参数，`message_agent(to=<id>)` 按行首 id 私聊）；表头明说「照抄 @token，不要带引号」。解析器口径未改，前端 `mentionPattern.ts`
仍与之一致。

**口径（2026-09-13 第八轮收口）**：附件 marker 不再维护「逐字段出处」清单——那种清单每轮都会漏一格
（上一轮漏的是 `mime`：`sniff_mime_type` 最后一层回显外部声明的 Content-Type）。marker 与当前轮附件列表的规则是
「每个值都编码，只有语法固定字面量裸露」，按构造成立，与字段来自哪里无关。其它块（roster、工作板等）的
作者可写字段仍按上表处理；`status` 白名单、`_member_status`、时间戳、计数这类取值集合封闭的平台值不加引号。

**明确不纳入**：团队 `description` / `intro_md`——owner 在管理 UI 写的散文（`intro_md` 按设计是多行
markdown），owner 就是这个 prompt 的委托方，不存在跨主体伪造。

## 2026-09-10（PR #394 review C1/M1）— lane 与 patrol 认领的探测现在有人结算

上一版两处认领点只认领不结算（bus 路径不经 `BackgroundRun`）：死凭据每 5 分钟重跑一个真
turn 且延迟永不翻倍，修好的凭据回不到 ACTIVE。现在 `try_begin_probe` 返回的
`probe_token` 经 `_handle_channel_batch(probe_token=)` / `_patrol_body` → `_invoke_runtime(
probe_token=)` → `run_and_collect(probe_token=)`，由 [[client]] 在 events 行终态后按 run 结果
结算（fatal → 带 runtime 原始 error_type 的失败；成功 → 关闭熔断；用户停止 → 归还）。两处出口
都兜一次 `release_probe(agent_id, token)`：lane 用 `try/finally`，patrol 用
`stack.push_async_callback`——turn 没走到结算就抛了/被取消时把名额还回去，已结算则 CAS 不匹配
no-op。普通 turn（无 token）仍不喂熔断器，与 #117 之前一致。lane 顶部 `should_skip` 的
`GateVerdict` 作为 `prior=` 交给认领，普通 turn 只读一次；patrol 的派发读隔了一个周期，认领时
重读。锁：`test_bus_circuit_breaker_gate.py::test_a_claim_the_turn_never_settled_is_handed_back`
（真熔断器）、`test_invoke_runtime_hands_the_token_to_the_client`、
`test_granted_probe_runs_the_relevant_batch`（token 随 batch 走）；结算本身见
`tests/agent_runtime/test_client_probe_settlement.py`。

## 2026-09-10 — 半开探测认领下移到真正起 turn 的那一点

[[circuit_breaker]] 的 `should_skip` 现在是纯读；探测名额由 `try_begin_probe` 认领。
上一版把认领放在 `should_skip` 里时，`_process_lane` 后面的 IM 前缀 ack、@mention 过滤
ack、限流 ack 三条"不跑 turn 就返回"的路径会把 3 秒一轮的 poller 变成探测名额的头号
浪费者（群聊房间里 @mention 过滤是常态路径）。现在两处认领点：`_process_lane` 在
`_assemble_lane_batch` 的 multipart hold 分支与限流检查之后、`_handle_channel_batch`
之前（一条还在分段到达的长消息 hold 住 lane 时同样不消耗探针）；`_patrol_body` 在 speech cap 之后、拼 prompt 之前
（sweep 的 `should_skip` 仍在 `_dispatch_patrols`）。被拒与 skip 同义：消息不 ack、留队；
patrol 走 `finally` 的 `mark_patrolled`，不会变成热候选。
`test_mention_filtered_batch_does_not_claim_the_probe` / `test_held_multipart_batch_does_not_claim_the_probe`
/ `test_refused_probe_leaves_relevant_batch_queued` 钉住"过滤/hold 路径不认领、被拒不 ack"；
这些测试用真 `BusMessage`（lane 会读 part_* 字段做 multipart 组装，薄替身会在那一步炸掉）。

## 2026-09-10（PR #389 M3/M6）— drop 通知与 arm 包进 try；三段 assemble 抽成 `_assemble_lane_batch`

`_wake_sender_on_drop` 的 `announce_processing_failure` + `arm` 原本在 `except` 处理块里裸 await，
抛了会从 except 逃出去（最终被 `_process_lane` 兜住、只丢一行 `[bus-timing]`），现在自带 try。
`_process_lane` 的「窄读 → 宽读 → 最后一次裁决」三段抽成 `_assemble_lane_batch(...)`，三步显式
命名，行为不变（`test_multipart_messages.py` 全部用例钉着）。

## 2026-09-10（PR #389 I4）— 指纹口径只有一家：`_batch_fingerprint(batch)`

返回 `(content_key, 本批全部行 id)`，`_stamp_receipts`（写）与 `_drop_already_announced` /
`_silence_already_announced`（读）三处都调它；此前三处各自手写 `"\n".join`，任一处漂移就让两个
防乒乓守卫同时静默失效而测试全绿（各测各的路径）。回退实证：让写入方改用 `"|"` 拼接（`content_key`
会归一空白，所以换行数量的漂移是看不见的），指纹类测试全红。写入方仍是**每批一个**指纹（不进循环）。

## 2026-09-10（PR #389 I2）— 静默唤醒补第二层界限：(收件方, channel, `no_reply_peer`) 窗口

指纹只挡原文重发；发件方模型读到「对方没回」会**换个说法**再问——指纹不同、守卫不命中、再唤醒，
每轮双方各烧一个 turn。现在与掉包路径对称：先查 `owner_notice_cooldowns` 的
`(agent, channel, "no_reply_peer")` 窗口（**新类别**，不复用 owner 收件箱的 `"no_reply"`——共用会让
「owner 已被告知」吞掉「发件方还没被唤醒过」），再查指纹；通知落地后 `arm`。闸门是「peer 是 agent」
而非 `wake_peer`：DM 里任何一行都会起对方的 turn（mention 与否无关），errand-continuation 批次
上那条不带 mention 的通知同样唤醒；人的沉默不占窗口。锁：
`test_a_rephrased_question_after_a_silence_does_not_wake_the_sender_inside_the_window`；
`..._wakes_the_sender_once_per_content` 的第三步改为先让窗口过期。

## 2026-09-10（review r3 M6）— drop 守卫读失败时既通知也不 arm

两次 fail-open：表读不到 → 照样唤醒，且本轮不设窗（arm 大概率同样失败）；下一次能读的 poll
重新建窗。写进 `_wake_sender_on_drop` docstring。

## 2026-09-10（review r2 I1）— 掉包指纹层按整批算，并排除本批全部行

`_drop_already_announced(agent_id, channel_id, batch)` 与 `_silence_already_announced` 同构：
指纹 = `_receipt_worthy(batch)` 正文按 `\n` 拼接（与 `_stamp_receipts` 写入口径一致——此前
只算 trigger 那一条，两条以上的批次永远查不到，第二层守卫形同虚设），`exclude_message_ids`
= 本批每条（含分片每行）的 id——本批自己刚写的 dropped/silent 行必须全部排除，否则两条消息的
批次会把自己的第一次唤醒压掉。`_wake_sender_on_drop` 多收 `batch=messages`。锁：
`test_the_same_content_dropped_again_is_recognised_by_fingerprint`（两条批次、删冷却行）、
`test_a_silent_two_message_batch_still_wakes_the_sender_once`。

## 2026-09-10（review r2 C2）— WIDE 重读是最后一次判定，不再无条件 hold

`_process_lane`：wide 重读后若仍 `truncated and not relevant`，再以 `batch_truncated=False`
调一次 `assemble_parts`，让 grace / 取代正常裁决。此前这条路径 `return False` 且不 ack 不通知，
一个死掉的两块组 + ≥500 行积压就让车道永久空转（每轮 550 次逐行 poison 查询）。**WIDE 是最后一次判定**，不再无条件 hold。
LIMIT 属于整条 lane 而不是组：合法组在**两块之间没有 ≥500 行普通消息插入**时不会被 WIDE 切断
（写入边沿「同 sender 最近一块」链接、跳过普通行，所以普通行可以合法地夹在两块之间）；真被
切断时宁可按 grace 裁决——已过 grace 的组按视野内的块投出并打 missing 标记、其余块随后作为
第二个残片再投——也不永久 hold；年轻的组这一趟仍被 grace 扣住（r3 M2 锁）。代价=极端积压下
可能多打一次 missing 标记（r3 I2）。锁：`test_a_stuck_group_behind_a_deep_backlog_does_not_deadlock_the_lane`（641 行）。

## 2026-09-09（review I2）— 批次被 LIMIT 切断时不对组下结论

`_process_lane` 显式传 `limit=PENDING_BATCH_LIMIT`，`len(messages) >= limit` 即视为「批次被切」，
`assemble_parts(..., batch_truncated=True)` 对不完整组只 hold；若 hold 后 deliverable 为空
（组顶在批次开头、无从推进），改用 `PENDING_BATCH_LIMIT_WIDE`(500) 重读一次再判。原注释
「MAX_MESSAGE_PARTS < 50 保证整组在一批里」是假的（LIMIT 是整条 lane 的），已删。
锁：`test_a_group_cut_by_the_batch_limit_is_delivered_whole`（把 LIMIT 打成 2）。

## 2026-09-09（review I3/I6）— hold 只扣未完成的组及其后续，ack 高水位=已投递最新行

`_process_lane` 里 `assemble_parts` 改回 `(deliverable, held)`：`deliverable` 已按时间排序并去掉
被扣的尾部，`relevant[-1]` 同时是触发消息与 ack 高水位，永远不会越过被扣的行；组之前的无关
消息立刻投递，不再整批陪跑 600s。空 deliverable 才 `return False`。不变式与理由见 [[multipart]]。

## 2026-09-09（review I1）— 两张新账本挂进每日 retention tick

`_maybe_run_steer_cleanup` 同一 tick 顺带清 `bus_delivery_receipts`（`RECEIPT_RETENTION_DAYS=30`）
与 `owner_notice_cooldowns`（`NOTICE_COOLDOWN_RETENTION_DAYS=2`），各自独立 try。两个阈值都远
大于它们服务的 30 分钟窗口——扫进活窗口等于重新打开它（B-20.3 反向复发），
`test_steer_routing.py::test_the_daily_tick_also_sweeps_the_two_bus_ledgers` 钉了这层关系。

## 2026-09-09（review C2）— 掉包唤醒发件方加窗口：同一 (收件方, channel) 每窗一次

`_wake_sender_on_drop` 之前没有任何上限：通知唤醒 A → A 换措辞重发 → B 再崩 3 次再 drop →
再唤醒——B 坏一天这条 DM 就几千轮（8/17 Liam 乒乓的形状，这次由平台点火）。现在两层守卫、
共用 `FAILURE_NOTIFY_COOLDOWN_SECONDS` 一个常量：① 回执账本 `prior_outcome(status=dropped)`
——同 channel 同收件方对**同样内容**（`content_key`）窗口内已 drop 过；② `owner_notice_cooldowns`
按 (收件方 agent, channel, `"peer_drop"`) 的窗口，兜住换措辞。通知真的落地才 `arm`；守卫读
失败 fail-open（多唤醒一次比藏掉一次便宜）。窗口而不是永久：B 修好又坏必须重新告警。
`_silence_already_announced` 同步改走带窗口的 `prior_outcome(status=silent)`（review I1 的
「永久压制」也由此消失）。锁：`test_delivery_receipts.py` 末尾两条（每窗一次 + 指纹层独立生效）。

## 2026-09-09（review C3）— 分片消息的失败按**每一行**记

`_handle_channel_batch` 的 except 分支对 `trigger_message.part_message_ids or [message_id]`
逐行 `record_failure`。合成消息的身份是第 1 块，而 `get_pending_messages` 的 poison 过滤是
按行的：只记第 1 块 → 第 1 块被滤掉、2..N 留在队列变成无头组 → hold 满 grace → 当碎片投递
并谎称「part 1 never arrived」→ 再崩。`dropped` 判定仍读第 1 块的计数（每块计数相同，
求和会让阈值提前 N 倍）。锁：`test_multipart_messages.py::test_a_merged_message_that_poisons_leaves_no_part_behind`。

## 2026-09-09 — 分片消息在车道入口重组；同一沉默只唤醒发件方一次（8/31 A2A 长消息复盘）

**重组**：`_process_lane` 在 mention 过滤之后、限流之前调 [[multipart]] `assemble`：组还没
到齐且年轻 → 直接 `return False`（不 ack，最后一块的 wake bump 会把车道拉回来）；到齐 →
turn 收到**一条**合成消息（`_build_prompt` / `build_bus_anchor` 都只见一条）。这是 8/31
「收件方 turn 是空的」的根：wake 在第 1 块上就起了 turn。

**重发守卫**（`_announce_undelivered_turn`，仅 DM 分支）：undelivered 通知本身是 DM 里的一条
消息，会起发件方下一轮；模型读到「没回复」就原样再发，得到同样的沉默、同样的通知——
乒乓。现在通知前先问 `_silence_already_announced`：收件方是否已在本 channel 对**同样内容**
（`content_key`，来自 `_stamp_receipts` 写进回执的指纹）在另一条 message 上沉默过。是 →
只写回执 + owner 收件箱通知（有冷却），**不贴通知、不 @**，发件方不会被第三次唤醒。
守卫读失败 fail-to-False（多唤醒一次比藏掉一次真沉默便宜）。`batch` 参数把整批传给它，
指纹口径与 `_stamp_receipts` 一致（peer 正文按 `\n` 拼接）。
锁：`test_multipart_messages.py`（12k 往返 / hold / 取代 / 过期标记 / 一次唤醒）。

## 2026-09-09 — 投递回执 + 被丢弃的消息回写给发件方（上游 #106 第 2/4 子项）

DM 车道（`not is_team`）在 turn 结束时把结果写进 `bus_delivery_receipts`
（`_stamp_receipts`，[[bus_delivery_receipt_repository]]）：有工具触达→`processed`；
只有 owner 文字（走 `_write_to_inbox`）→`relayed`；`reached_nobody`→`silent`；抛异常→
`failed`（attempts=失败计数、reason 已脱敏）；到 `POISON_FAILURE_THRESHOLD`→`dropped`。
团队房不写回执：发件方本来就盯着房间。人（`usr_` 前缀）与平台行不算「等回执的发件方」
（`_receipt_worthy`）。回执写入 best-effort 永不抛——账本不能把已投递的一轮变成失败。

**丢弃时唤醒发件方**（`_wake_sender_on_drop`）：此前 poison 阈值只走
`_notify_permanent_failure` 写收件方 owner 收件箱；发件 agent 毫无信号，就是 #106
「PM 说开工了、Web Developer 已经崩了三次」的形状。现在同时调 [[delivery_notice]]
`announce_processing_failure`：在该 DM 里贴一条 `system_delivery_failed` 并 @ 发件方，
让它下一轮开在失败上。仅当发件方是 agent（人没有 turn 可唤醒）且触发消息不是平台行
（不对通告发通告）。`_stamp_receipts` 同时写 `content_key`（本批 peer 正文的指纹），
是下一条 commit 里「同一沉默只唤醒一次」的依据。锁：`test_delivery_receipts.py`。

## 2026-09-09 — owner 通知冷却窗持久化，键改为 (agent, channel, category)

`_notify_owner` 的 `cooldown_key: str` 参数换成 `cooldown_category: str`，窗口不再是
`self._notify_cooldown` 进程内 dict（已删），而是查/写 `owner_notice_cooldowns`
（[[owner_notice_cooldown_repository]]），`target=channel_id`。两处调用方随之变化：
永久失败通知 `cooldown_category=category`（`provider_credential` / `generic`），
「没回复」通知 `cooldown_category="no_reply"`——后者原先刻意按 agent 不按 peer，现在按
channel：对 B 沉默与对 C 沉默是两件事，通知正文本来就点名 channel。

为什么要动（上游 #106 交接失联复盘的第三子项）：旧 key `agent:category` 让 channel A
上一次永久失败把 channel B 上无关失败的通知按掉 30 分钟；重启把窗口全忘、下一轮 poll
重发；多容器各自为政。窗口读失败 **fail-open 照样通知**（重复比漏发便宜），
`arm` 仍只在收件箱写成功之后。锁：`test_failure_notification.py` 新增
per-channel 与「换一个 trigger 实例仍被压制」两条。

## 2026-09-07 — 两处消费方跟着注册表视图走

`im_channel_prefixes()`（专属 trigger 渠道的 channel_id 前缀，用来防止 bus 二次派发）现在
读的是 `ingress.channels` 上的消息来源视图，不再是 import 期写出来的全局字典。所以一个
渠道的前缀在它的描述符进注册表那一刻就被覆盖，在发行版排除它那一刻消失——不再取决于这
个进程 import 过谁。

`_bus_turn_delivered` 里「message_bus handler 没注册」那条 warning 的措辞跟着改：现在的
真实原因是 `builtin.message_bus` 被禁用/排除，或这个进程压根没 boot 插件平台，而不是
「一次被吞掉的重复注册 ValueError」——后者已经不可能发生了。fail-to-True 的语义不变。

## 2026-09-03 — 团队房沉默不再贴系统行；prompt 从「全都回」改成「回有实质要求的」

owner 反馈「team 协作太啰嗦、agent 之间交互繁琐」,实测 dev 一房 19 字提问→23 条接力。
本文件两处:

- **`_announce_undelivered_turn` 的 `is_team` 分支**:不再 `announce_undelivered`,
  改调 `_bus_activity.note_silent_turn`(直接 import 私有模块,门面只公开读侧)把沉默记到
  成员活动行(花名册 idle/queued 态显示「上一轮未发言」),并打一行
  `[team-turn] silent agent=… channel=…` INFO 日志——旧的 `msg_type='system_undelivered'`
  计数改用这行日志统计。
  A2A 私聊分支原样(对方真的在等,靠这条唤醒;owner inbox 通知也只剩这条路)。
  `wake_peer` 少了恒真的 `not is_team`。
- **`_build_team_prompt`**:①默认应答者:「Answer it yourself. Hand it to a teammate
  only when the work is clearly theirs — and then hand it over, do not also
  half-answer」,不再「hand it to whoever is the better owner」;②批量 @:
  「Address ALL of them」→「Reply to the ones that ask you for something … one that
  only informs you, or confirms what you said, needs no reply」;③交付规则:沉默
  「leaves no mark in the room」;④写作规则加两条:只说新东西(不 收到 开头、不复述队友
  已接受的、不发 wrap-up 除非用户要)、只回问你的人(别为了「同步」@ 全屋,每个 @ 都是一轮)。
  锁:`test_team_reply_discipline_prompt.py`;`test_default_responder_honesty` 锚点改
  「Reply to the ones that ask you」。prompt 不是守卫(铁律 #15),守卫是 [[errand]] 的
  `opens_handoffs` 和这里的沉默标记。
  **残留**:成员→成员的「完成后交给你 @A4」不再上板,那种形状只剩 Dunhuang 那条 prompt 规则
  在管(errand.py 明知的取舍);团队房也不发 owner inbox 通知(owner 就在房里,花名册有标记)。
  文案:`chat.team.guide.relay`(zh/en)不再写「4 次」,改为「达到平台上限后停止并在群里说明」。


## 2026-08-21 — patrol 注入的「别调发送工具」对齐 suppression 实际范围（🟢 review 收尾）

PR#339 合并后 🟢 收尾 + PR#341 增量审的 3 Important 深修——**`_build_team_prompt` 内部的 patrol 自相矛盾这次修到根**:

- **patrol 注入点名两个动词**：原只写 `do NOT call message_team`,但 patrol 轮 `disallowed_tools` 把 message_team + message_agent **两个都摘**。改为 `do NOT call message_team or message_agent — neither of those two calls is available on this turn`(措辞去掉 agent 面不该出现的内部词 `bus`/`desk`,Minor)。让 [[message_bus_module.py]] 静态块那句 `unless this turn's own prompt says otherwise` 的对冲在两个动词上都由本轮 prompt 显式否定兜住。**只点这两个 bus 动词**——`reply_owner`/`notify_owner` 在 patrol 不被摘（ChatModule patrol `return []`），写进去成假声明。
- **⚠️ 交付机制块下放非 patrol 分支（Important）**：同函数 165 行后那段 `Speak in this room by calling message_team(...). Rules:` + `Nothing you write outside that call reaches the room` + `make no call at all` + `Put ONLY the message in text` + action-tools,原来**无条件**执行——patrol 轮也拿到,于是同一 prompt 既禁 message_team 又命令调 message_team,且「outside the call 到不了房间」在 patrol 上字面为假（平台就是把纯文本贴成房间）。**用 `if patrol_stalled is None:` 把这几条 delivery-mechanism 收进非 patrol**;surface-无关的写作/@mention/Dunhuang 规则保持无条件(patrol 也 @ stalled owner、也不许承诺)。判别式复用现成的 `patrol_stalled is not None`,不新引第二个 flag。
- **测试锁**：`test_patrol_turn.py::test_the_patrol_prompt_forbids_both_bus_verbs_and_never_orders_message_team`——断言 patrol prompt 的「do NOT call」行同时含两个动词,且**不含**「Speak in this room by calling message_team」「Nothing you write outside that call reaches the room」。非 patrol 的团队-prompt 契约测试(`test_visibility_wording.py` 里那几个)都不传 `patrol_stalled`、走非 patrol 分支,不受影响。`test_expressive_collection.py:361` docstring 引文同步补全。

## 2026-08-20 — 通信能力跟着 agent 走：文案跟随 module 改动

配合 `message_bus_module` 删掉 trigger-channel 的发送动词 drop（详见该文件 mirror 同日条目）。本文件两处改动，均无控制流变化；其中「## Answer the peer」是**发给 agent 的 prompt 正文**，措辞改了 = agent 收到的指令随之变（属提示面变化，非纯注释）：

- **`BUS_TEAM_ROOM_EXTRA_KEY` 注释**（trigger_extra_data 组装处）：从「disallowed_tools drops message_agent off the desk / 否则 post team replies into the wrong conversation」改为「这只是**默认 reply reminder** 的标记；不再删 peer 动词，每个内部发送动词每轮都可达；删掉这个标记只是把 reminder 默认翻成 peer 动词」。`team_room=is_team` 传参不变。
- **「## Answer the peer — REQUIRED」注入** 第 1 点：从「reply to the asker … This is the point of the turn」软化为「usually the point；如果对方要求的事需要去别处行动（发团队群、私聊别人），你这一轮也能做——你的 teams 和 peers 都在 context 里」。不再把 `message_agent(to=sender)` 锁成唯一出口。

`is_team = channel_owner.startswith(TEAM_ROOM_OWNER_PREFIX)` 与 patrol 标记逻辑不变。

**Dunhuang 那条（`_build_team_prompt` 的「Do not promise future delivery」）措辞解耦**（review 第 8 条）：旧句「Sending this message ENDS your turn」字面读作「发一条就结束」,和新授予的「同一轮可发去别的会话」矛盾。改为「Nothing of yours keeps running once this turn ends」——只否定它真正要否定的（turn 结束后没有你的东西在跑,所以「完成后交给你」是空头承诺）,**Dunhuang 禁令本身与三个出口(本轮做完/说清进度/`job_create`)一字不动**（铁律 #15,errand.py 看板兜底不是替代品）。`test_errand_auto_board.py::test_the_team_prompt_names_the_alternatives_to_a_promise` 同步改断言。

## 2026-08-19 — team「是否说过话」区分「判不了」与「确实没发」

`has_message_from_turn` 的判据是 `event_id` 身份 join，而 event_id 来自 MCP 请求头、合法地会缺失（identity 从不是 flow control）。改为：`can_judge = bool(turn.event_id)`；判不了时**假定已投递**（`posted = spoke or not can_judge`），只有 event_id 在场且 join 为空才 `_announce_failed_room_post`。此前缺 header → `spoke=False` → 在一条已在房间里的回复下贴假「never sent it」通告 + `_hop_done` 低估投递率。由 `test_team_delivery_e2e.py::...without_an_event_id` 钉住。

## 2026-08-17 — 板子进 prompt 加上限（review 🔴3 的第三项）

`TEAM_BOARD_MAX_ITEMS = 15`。板子此前全量渲染进**每个成员每一轮**的 prompt，而
在 [[errand]] 之前每一行都要 Leader 一次显式工具调用，稀少是天然的；现在行数跟
着房间流量走。

溢出**明说**（`+N more not shown`）而不是悄悄截断：读起来完整的截断会让 lead 以
为剩下的已经关掉了——铁律 #16 对用户可见流的要求，同样适用于模型看自己团队的那
一眼。保持 oldest-first：最可能卡住的就是开得最久的，那正是这一段存在的理由。

## 2026-08-15（二）— 交接自己上板子 + 团队房禁止承诺

两处改动服务同一件事：**工作板的入口不再压在模型服从上**。

**`_record_errands`**：团队回帖**投递落地后**，先结清本人在这个房间欠的差事，
再把这条帖子 @ 到的人开成新差事（[[errand]]）。顺序固定 close → open：在敦煌那
条消息（「收到，开始处理……完成后交付 @A4」）上 close 是空操作——它是承诺不是交
付——而 open 补上下一棒，于是**两跳都还被盯着**。反过来会让一次交接把自己刚创建
的差事关掉。

在此之前板子的唯一入口是 `work_add_item`，Leader 忘了调板子就是空的，巡查无事
可扫——断链那一轮正是如此。

**挂在 `_deliver_reply` 里，且在它的 `try` 之外**。同批 rebase 上来的 #291 把投
递搬进了 turn 内部，所以钩子跟着搬。放在 try 外面是必须的：那个 try 回答的是
「这一轮有没有够到房间」，记账绝不能让一次已经送达的回帖把自己报成丢失。
`_record_errands` 自己也吞掉全部异常，同一个理由。

**用的是 cap 之后的 `mentions`**：被级联上限剥掉的 @ 从没到达那个队友，给它开差
事等于让一个没被问过的人欠着账。

**`_post_to_room` 改为返回 message_id**：差事的去重键是**消息**，而重读房间去找
刚写的那行会和这个方法刚唤醒的轮询循环打架。

**团队 prompt 增「禁止承诺未来交付」**：把 IM 1:1 早就有的那条（
[[channel_prompts]] 的 DIRECT 协议）补进团队房——在此之前敦煌那句话在团队房里
**完全合规**。措辞带上三个出口（这一轮干完 / 说清进展 / `job_create`）而不是光
禁止：0802 微信那次证明了只说「不要」会让沉默成为合规答案，而沉默正是这个房间
最付不起的。它**降低**平台护栏被触发的频率，**不是**护栏本身（铁律 #15）。

## 2026-08-15 (三) — 宽 `except` 会把测试桩的契约偏差吞成"绿"

删掉 `TurnResult.segments` 时漏了一个测试桩，它还在传 `segments=`。frozen dataclass 必然
抛 `TypeError`——而这个异常落进 `_handle_channel_batch` 的宽 `except`，被记成一次
`record_failure`，**测试仍然是绿的**。

绿得还很有欺骗性：桩的执行顺序是「填 sink → 调 deliverer（房间行已经落库）→ 才 return」，
所以断言"行存在且 segments 正确"在崩溃之前就已经满足。`_invoke_runtime` 之后的整段——游标
推进、`post_state` 三分支、fatal 播报、hop 观测——在那条测试里一步都没走过，而它的 docstring
还写着自己是"唯一一条会自己变红的测试"。

结构性的教训：**只要一个 turn 被宽 `except` 包着，任何 stub 与真实签名的偏差都会静默降级
成"这一轮失败了"，而断言可能早就满足了**。所以驱动真 turn 的测试现在都加一条
`_assert_turn_survived`（这一轮没有被记成失败），断言落在 `_invoke_runtime` **之后**的代码
上。把这条加回去之后，重新引入那个多余参数会让 5 条测试红。

## 2026-08-15 (二) — `TurnResult.segments` 删掉：一条只有写、没有读的通道

发帖搬进 turn 内之后，边界改从 `segments_sink` 读（见 [[run_collector.py]]），
`turn.segments` 的最后一个读者就消失了——只剩 `_invoke_runtime` 里那一行写入，和一条用
源码文本把这行写入钉住的测试。

留着的代价不是那几个字节：下一个人会看到一个"看起来是数据来源"的字段，据此写出一个永远
读到 None 的分支；而那条源码断言会让"删掉死代码"这件事红。所以先删通道、再补 sink 的测试，
顺序反过来的话，新测试会顺手把死字段一起钉进去。

## 2026-08-15 — 和 #291 的和解：cap 与 segments 搬进 turn 内

#291 把房间回复搬进了 turn 内部（`_deliver_reply`），而 @mention 解析和 hop cap 也跟着搬了
进去——这条分支的两样东西恰好都长在那里。

**segments**：`turn.segments` 在 deliverer 被调用时还不存在。改成从 `segments_sink` 读
（见 [[run_collector.py]]），deliverer 在发帖那一刻 join 一次。

**cap 通知**：capping 在 turn 内发生，通知必须在 turn **之后**发——房间要读成"agent 先说话，
然后平台解释它拒绝了什么"，反过来会让平台的注解压在它所注解的那句话上面。所以 cap 的结果
（具名的人 + 是不是 `@all`）用一个 holder 带出来，和 dev 用 `room_post` 带出投递状态是同一
个手法。

通知只在 `post_state == POST_OK` 时发：在一条谁也看不见的回复旁边解释"我没把人拉进来"，
解释的是错误的那个缺席。

## 2026-08-14 (三) — `_post_to_room` 少了 `segments`，一次合并干掉了所有团队回复

`_post_to_room`（#303 引入的"本进程唯一的发房间入口"）**显式列出参数**，理由写在它自己的
注释里：`**kwargs` 会把拼错的关键字藏起来，只在运行时炸成 TypeError。

而 segments 是在另一条分支上加到回复里的。两者并行落地，git 干净地合上了，结果是**每一次
团队 turn 都在这里抛 TypeError**——被调用方那个"回复存在但房间永远看不到"的处理器接住，
于是房间收到的是一条投递失败通知，而不是回复本身。生产环境里响亮，测试里隐形：segments 的
测试要么直接调 `send_message`，要么断言 trigger 的源码，两类都是绿的。

**真正报警的是上一轮加的四个 cascade 端到端测试**，因为它们跑的是真 turn——而它们失败的
原因和 cascade 毫无关系。

显式参数列表的代价就是它落后时的代价：凡是 `send_message` 接受、且房间调用方会传的东西，
这里必须同时出现。现在 `tests/message_bus/test_team_message_segments.py` 里有一条**通过
trigger 发一条团队回复再把行读回来**的测试；把修复的任意一半撤掉它就红，而那个文件里其余
所有测试都察觉不到。

## 2026-08-14（二次）— 饥饿判定改用墙钟，因为周期在饥饿时会变稀

第一版按「连续 N 个轮询周期」计数，单测全绿——**因为测试循环里周期是瞬间**。真机验证
（`bus_max_workers=1`、两个 agent、@all）打脸：一次**真实 28 秒**的饥饿只产生了 **4 个
周期**，而阈值是 5，告警根本没响。

根因是自反馈：饥饿时 `_poll_cycle` 派发数为 0（候选都卡在信号量后面），于是自适应间隔
从 3s 一路退避到 12s——**采样频率恰好在最该采样的时候降下来**。用周期计数当阈值，等于
让被测量的现象自己决定测量精度。

改成 `STARVATION_ALERT_AFTER_S = 20.0` 墙钟。测试同批换成注入的假时钟，并新增
`test_cycle_frequency_does_not_change_the_verdict`：同样 30 秒的短缺，采样 2 次和采样
40 次必须给出同一个判定；退回周期计数会让 `checks=2` 那条挂。

真机复验通过：`service_audit` 落到
`{"stage": "worker_starvation", "starved_for_s": 27, "running": 1, "waiting": 1,
"max_workers": 1, "longest_running_agent": ...}`。

## 2026-08-17 — team 回复改成工具调用，投递不再是 trigger 的事

`_deliver_reply` / `include_monologue=is_team` / `on_plain_text_delivery` /
`live_segments` / `POST_OK|FAILED|NOT_ATTEMPTED` 全部删除。房间收
`message_team`，落点在 [[team_posting]]。

**事后记账从「平台投递成功了吗」变成「agent 这一轮在房间里说过话吗」**，而这个问题
`has_message_from_turn` 本来就能答（它此前只在 `POST_NOT_ATTEMPTED` 那一支被用到）。
于是整块三态状态机塌成一个布尔。**刻意不用 `turn.delivered`**：那会把
`notify_owner` 也算进去，而只通知了 owner 的 agent 把房间留在沉默里
——正是通知存在的理由。

**级联上限的叙述从这里移走**（搬到 team_posting，因为施加上限的是它）；两处都留会说两次。

**fatal 通知保持不变**，仍以房间自己的标记发、且不问 agent 有没有说过话：这条通知声称的是
**这一轮失败了**，不论如何都为真。

### `include_monologue` 只剩 patrol，而它必须剩下

patrol 是**另一件事**：平台请 lead 撰写房间的状态行，然后以**房间自己的标记** +
`msg_type=patrol` 发出去。工具做不到这件事（工具以 agent 身份发，那条线会被算成 agent
一跳、并读作 lead 在闲聊）。而 NexusPower 把 agent 的纯文本走 AGENT_THINKING，**去掉这个
开关会让 patrol 在 nexus_power 上静默失效、在 claude_code 上看起来正常**——本仓吃过两次的
那个形状。守卫从「team 批次必须开」翻转成「team 批次不许开 + patrol 必须开」
（`test_team_monologue_wiring`）。

patrol 的 prompt 现在**自己说明**这条例外（P1：surface 特有的事实由该 surface 自己讲）：
「这一轮你在撰写房间的状态行，不是以自己的身份说话：写成纯文本、不要调 message_team」。

### 安全网（与投递变更同批，不可拆）

briefing-squad 那个 P0 重新进入射程——纯文本自动上墙时它结构上不可能发生。三层网：

1. **`message_team` 是本轮声明的默认回复工具** → 两个框架的 reply reminder 都会渲染它。
   **这是主网**，且两个框架都覆盖。
2. **哑轮通知**：turn 产出了文本但房间里什么都没有 → `_announce_failed_room_post`
   （措辞改为「产出了回复但从未发给房间」）。平台**不**替它写（铁律 #15），但损失不再静默。
3. **`expression_nudge`**：team 轮次传一个最小 `TurnProfile`（只开这一项，其余全默认；
   `narrative_persistence` 只在 `bm25_top1` 快路上被读，本 profile 不走那条）。仅
   NexusPower 有。

**刻意没用 IM DM 那条 helper-LLM 兜底。** 那条路让 helper 写回复、平台投递——在 1:1 里
可以接受，在 team 房间里是**平台冒充一个 agent 在它的队友和 owner 面前发言**。spec §5.2
建议把 team 从兜底排除名单里摘出来，这里没有照做，理由就是这一句。

## 2026-08-17 — 唤醒终于跨进程了（`_wait_cross_process_wake`）

2026-08-14 那条的末尾写着「要跨进程就得上 DB 信号 + 读取方，**等 peer DM 延迟真成为
抱怨再说**」。现在必须做了，原因不是抱怨，是**契约变了**：team 回复改成 MCP 工具
（`message_team`）之后，房间自己的接力**搬到了进程内 Event 从来覆盖不到的那条路上**。
不做的话，把 team 投递改成工具会把 `c7739ad1` 量出来的延迟收益吐回去一部分——而且是以
「房间安静了」的形式被用户感知到，正是铁律 #16 禁止的那类退步。

`_sleep_until_due` 现在同时等三个：stop、进程内 `_wake_event`、以及
`_wait_cross_process_wake()`。第三个每 `WAKE_SIGNAL_SLICE`（0.5s）读一次
[[wake_signal]] 的单行信号，变了就返回。

**为什么读信号而不是直接跑 pending 扫描**：那个扫描正是这套东西要调度的昂贵查询，每
0.5s 跑一遍就把目的抵消了。单行读，每秒两次。

**fail open 且安静**：信号读不到 = 没有新消息，退回调用方的 timeout。为一个延迟优化把
poll 循环搞崩是本末倒置。但「安静」不等于「不可观测」——能发现信号失效的是
`[bus-timing]` 里的 `queue_wait`（铁律教训 #4：只有 L1 存活检查是僵尸的后门）。

**连带**：`_post_to_room` 与那条结构性守卫测试的立论消失了（见 [[local_bus]] 同日条）。
唤醒现在在 `send_message` 里面，调用方无从遗漏。该测试待随 team 投递契约改动一并处理。

## 2026-08-14 — 投递即唤醒（`_wake`）+ 槽位饥饿告警 + `MAX_WORKERS` 配置化

三件事，都在 `start()` 这条线上。

**`_wake` / `_sleep_until_due`**：验收⑤说的「零迹象窗口」不在一轮**之内**，在两轮
**之间**——A 跑完投递，B 在这条消息里被 @，然后 B 要干等一个完整轮询间隔（3-12s）才被
发现；三跳接力叠下来就是用户看到的大部分死寂。修法是一个 `asyncio.Event`：团队房投递
成功后 set，poll 循环的 sleep 同时等 stop 和 wake。房间自己的投递来调度下一跳，而不是
让定时器过一会儿才想起来。

`_sleep_until_due` 每轮都取消两个 waiter（包括已完成的那个，取消是 no-op），否则每个
轮询周期都会在 Event 上泄漏一个 waiter；`_wake_event.clear()` 放在 sleep 出口而不是调用
点——留着不清会让**下一次** sleep 立刻返回，把循环转成空转。

**覆盖范围**：本进程的**全部**投递——团队房回帖与 leader 巡查，两者统一走
`_post_to_room`。agent 通过 `bus_send` MCP 工具发消息是在 MCP server **另一个进程**，
进程内 Event 够不着，那条路仍走轮询。团队接力（PRD 的主战场）覆盖到了，peer DM 没有。
要跨进程就得上 DB 信号 + 读取方，等 peer DM 延迟真成为抱怨再说。

**`_post_to_room` 存在的理由不是"投递需要抽象"**（那就一行），而是**「投递」与「唤醒」
不可分割**。它们曾经是可分的，于是两个调用点里漏了一个：巡查以房间自己的标记发言、且会
@ 到成员，被 @ 的队友因此变成候选，却要干等一个完整自适应间隔——平台自己制造的死寂，比
agent 慢更难看。守卫是**结构性**测试
`tests/message_bus/test_bus_relay_wake.py::test_every_in_process_post_goes_through_the_waking_helper`：
本模块内除 helper 自身外禁止出现 `self._bus.send_message(`。第三个调用点想漏，得先绕开
这条测试。

**`_check_worker_starvation`**：`liveness_snapshot()` 从 2026-07-27 那次 wedge 起就带着
`running`/`waiting`/`max_workers`，docstring 也早写明「持续 running==max_workers 且
waiting>0 = 池子是瓶颈」，但没人读它。这条延迟上很要紧：**槽位等待就在 `[bus-timing]` 行的 `queue_wait_s` 里面**，也就是验收①判定所依据的那一列——没有这个信号，
池子饿死和「大家的 turn 都变慢了」长得一模一样。

三个刻意的性质：要**持续**满足 `STARVATION_ALERT_AFTER_S` 墙钟才算（单个瞬间满载是池子在正常干活）；一个 streak **只告警一次**（满载一小时是一个问题不是六十个，每周期都响的
告警没人看，教训 #3）；**纯诊断**——不取消、不停机、不改优先级（铁律 #14），也**不进
owner inbox**（槽位不够是平台侧的事，owner 动不了，塞进去只会训练他忽略 inbox）。

**`MAX_WORKERS` 挪到 `settings.bus_max_workers`，默认 3 → 8**。池子大小是**我们自己的**
资源决策，不是对 agent 的限制（铁律 #14）；写死成 3 让槽位短缺既看不见、又必须改代码才能
修。bus turn 几乎全是 await（LLM + DB），槽位很便宜，而一个团队房内部接力就能同时占掉
好几个。改完重跑 `make latency-report` 能直接看出差别——这是可测的改动，不是拍脑袋。

## 2026-08-14 — `[bus-timing]` 保持只进日志

一度加过「同时写 `bus_hop_timing` 表」，已撤回；理由见
`mirror/scripts/diag_collector/latency_report.py.md`。本文件因此回到只打日志行，
`[bus-timing]` 的格式与「失败的 turn 不算一跳」（`_hop_done`）的约定均未变。

## 2026-08-14 — cap 住 `@all` 的那一路要带着标记走

cap 触发时除了收集被拦下的**具名**队友，还要记住"被拦下的是 @all"（`capped_everyone`），
并传给 [[team_notices.py]] 的 `post_cascade_capped`。少了它，房间里最常见的那种被拦
（`@all`）是完全静默的。

这条路径此前**没有任何行为测试能到达**：唯一驱动它的测试把 `_invoke_runtime` 假成了
tuple，而生产代码读 `turn.text`，于是 `AttributeError` 被批处理的 `except` 吞掉，
`turn.text` 之后的所有逻辑一行都没跑过（游标在更早几行就已经 ack 了，所以断言照样绿）。
修好那个假对象之后，`tests/message_bus/test_team_cascade_notice_e2e.py` 真跑
`_handle_channel_batch`，三条读源码字符串的断言随之删掉——其中一条
（`assert 'm != "@everyone"' in src`）正好把这个 bug 钉成了验收标准。

## 2026-08-13 — 投递为空不再是静默：`TurnResult` 与三处兜底

PRD《看到的必须是真的》§四。此前 `_invoke_runtime` 返回 `(text, event_id)`，
投递逻辑是一句 `if response_text:` —— **空就什么都不做**。三个洞共用这一句。

**`TurnResult` 取代二元组，多出来的是 `delivered`。** 这是 `text` 永远回答不了的
问题：bus turn 有**两个**投递面，peer 只能被 bus send **工具**触达，而工具的产出从
不出现在 `text` 里。所以「`text` 为空」不等于「什么都没送达」——照着它下判断，会在
一条投递成功的回复底下印出「没有回复」。

`delivered` 的判据**问 MessageSource 注册表**（`is_user_reply_tool`），不在这里重打
工具名单：注册表已经是「哪些工具在 bus turn 上算投递」的唯一真源，第二份名单会在
第三个 send 工具出现的那天悄悄跑偏 —— 2026-08-01 的 no-reply 指标就是这么被污染的。

**`_delivered_to_anyone` 失败时返回 True（不是 False）。** False 的下游是一句公开的
「本轮没有投递」，注册表抖一下就会把这句谎话印在一条正常送达的回复下面。漏报一次真
沉默，用户损失的是他本来就已经在忍受的东西；**误报一次，损失的正是这整个改动要重建
的信任**。

**三处落点：**

- **团队房间空回复** → `announce_undelivered`（不带 mentions）。判据是
  `reached_nobody`，不是 `not text`：模型违规用工具把话发进了房间时，房间确实收到了，
  这时候还说「没有回复」是**反方向的同一句谎话**。
- **上墙失败** → `announce_delivery_failure` + `_write_to_inbox`。两种损失、两份补救：
  房间说「发不出去」，收件箱**留住发不出去的那段正文** —— 它已经生成、已经计费，
  一次写失败不是销毁它的理由。**故意不落到通用 except**：游标在几行之前已经推进，
  这条消息早已 ack，`record_failure` 只能给一次永不会重试的投递刷毒药计数。
  `_hop_done` 保持 False，`[bus-timing]` 量的是投递，把丢掉的回复算成一跳是自我美化。
- **A2A 无投递** → 带 `mentions=[提问方]` 的通知 + `_notify_undelivered_owner`。
  `errand_continuation=True` 时**不叫醒 peer**：那批消息是 peer 在回答我们的 errand，
  它没在等；等的是我们自己的 owner，收件箱那一条才是全部补救。

**ping-pong 闸**：触发消息本身就是 `system_undelivered` 时不再产生新通知。两个都不
说话的 agent 否则会互相甩平台行 —— 每一次沉默都在诱发下一条通知。

**它是第一个带 mentions 的平台类型**，也就是第一个能**成为触发消息**的平台类型 ——
[[system_messages]] 的 `trigger_label` 分派表当初正是为这一天写的。

## 2026-08-13（review 后）— 四处修正

narranexus-review 抓到并逐条修掉：

- **patrol 调用点漏改**（Critical）。`_invoke_runtime` 改成 `TurnResult` 后，全仓两个
  调用点只改了主路径，`_patrol_body` 还在 `response_text, _ = await ...` 解包 —— 不可
  迭代的 dataclass 会让每一轮 Leader 巡查在烧完一次 LLM turn 后抛 `TypeError`、写一行
  warning、戳游标、下周期重来，**永不再发 patrol 行**。测试桩全返回元组所以 CI 全绿。
  已改调用点 + 6 个桩为 `TurnResult(...)`。
- **ping-pong 闸从「只挡 `system_undelivered`」扩到 `in PLATFORM_MSG_TYPES`**。patrol
  行带 mentions 会成为被点名成员的 trigger，它被追到停滞、跑一轮仍无文本时不该读成
  「用户问了没答」。平台自己发起的 turn 没有在等回答的人。
- **`_delivered_to_anyone` 的 fail-open 补上「注册表静默降级」这一支**。
  `MessageSourceRegistry.get()` 从不抛，对未注册 source 返回默认 handler（只有
  owner-chat 工具），于是 bus send 不再算投递 → 每轮正确答复 peer 的 turn 都被扣上
  「没有回复」。现在校验 `handler.name == "message_bus"`，不一致也 fail-open 到 True。
- **owner 通知抽成 `_notify_owner` helper，加冷却**。此前 `_notify_undelivered_owner`
  每次 `reached_nobody` 都写一行收件箱，一个纯文本回话、不调 bus 工具的 agent 会在一
  条活跃 A2A 通道上每来一条消息刷一行同名通知，淹没共用一个收件箱的
  `_notify_permanent_failure`。冷却按 agent 聚合（`f"{agent_id}:no_reply"`），且沿用
  「写成功后才 arm」这条踩过的坑。`_notify_permanent_failure` 的 `trigger_message`
  参数已删（不再需要）。

## 2026-08-10 — patrol lane:poll cycle 的第二个候选源

`_dispatch_patrols` / `_dispatch_patrol` / `_run_patrol` 接入 `_poll_cycle`。
判定逻辑全在 [[patrol]],这里只负责调度与发言。

- **与消息派发同一道闸**:同一个 per-agent 锁 + 同一个 semaphore,并登记
  `_in_flight` —— 正在回话的 lead 不会被再叫醒来巡查,巡查也逃不出 worker 上限,
  卡住的巡查还能像普通轮次一样在心跳里被看见。
- **静默是常态**:模型没话说就什么都不发。每十分钟播报一次「一切正常」正是这个
  房间一路在删的常驻噪音(折叠 console、赖着不走的活动气泡)。
- **游标在 finally 里推**:崩掉的巡查也占用了它那一格,立刻重试会把一个坏掉的
  team 变成热循环。
- **以房间身份落墙**(`team_<id>` + `msg_type=patrol`),不是以 lead 的身份:
  这既是它能被 `_team_cascade_depth` 跳过的原因,读起来也诚实 —— 这是平台在
  盘点,不是 lead 在聊天。

## 2026-08-10 — `_team_cascade_depth` 跳过巡查行

拍板口径 (a)。不跳过的话豁免会自我拆台:巡查开口的那个房间**正因为流程断了**
才处在深度上限上,它自己那一行会把后续所有催办 @ 顶出可用区间。

用户消息仍然清零(计数器原本的用途没变)。

**过滤下推到 SQL,不在 Python 里跳过**:`AND (msg_type IS NULL OR msg_type != ?)`。
这不是写法偏好 —— 深度窗口是固定 `LIMIT MAX_TEAM_AGENT_HOPS + 2` 的定长窗口,
在 Python 里跳过的巡查行**仍然占着窗口的格子**。一个流程断掉的房间恰恰是巡查
最常光顾的地方,几条巡查行就能把窗口填满,`depth` 从此永远够不到
`MAX_TEAM_AGENT_HOPS` —— 防 @ 风暴的上限在最需要它的房间里静默失效。`IS NULL`
那一半是必须的:老消息没有 `msg_type`,漏掉它等于把历史全部排除在计数外。

## 2026-08-07 (三次) — lead 知道自己是 lead;工作板随 prompt 注入

`_build_team_prompt` 增加 `lead_agent_id` / `work_items`,`_team_board()` 负责
取数(best-effort:板子读不到就退化成「没有条目」,房间对话才是主表面 —— 丢一
段板子只是少点上下文,丢一轮是用户拿不到答复)。

**在此之前 `lead_agent_id` 在 agent 侧零语义**:全部消费者只有「无 @ 时的兜底
路由」和前端徽章。lead 不知道自己是 lead,所以「Leader 应该盯进度」根本无处
挂载。现在 lead 会拿到职责段:派活必须落 `work_add_item`,收到交付要
`work_complete_item`。

**板子是注入的,不是让它自己去查**:如果看自己的板子还得先调工具,「我派出去
什么」就取决于模型愿不愿意去看 —— 铁律 #15 要避开的正是这类依赖。普通成员也
看得到板子(它得知道自己认领了什么),只是不给驱动流程的职责。

## 2026-08-10 — owner 转发 wrapper 注解放宽为 Optional[str]

`resolve_owner` 拆分 ""(不存在)/None(查询失败)后(PR #258),本文件的转发
wrapper 如实透传 None;全部消费方按 truthiness/`or agent_id` 兜底,行为不变,
只是签名与 docstring 不再谎称"永远返回 str"。

## 2026-08-10 (方案 B) — team prompt 说明产出写哪里

共享目录那句补上：**要给团队看的东西也写这里**（报告、页面、准备注册成 artifact 的数据文件）。
自己 workspace 是私有的，留在那儿的工作队友打不开也接不下去。

## 2026-08-07 (二次) — 取消分支不设 `_hop_done`

rebase 时与 dev 的 `[bus-timing]` 埋点相遇。`_hop_done` 只在完整跑完一跳时
置 True,被取消的轮次刻意保持 False —— 让它进时延序列会把「投递要多久」
和「owner 什么时候按的停止」混成一个指标。

## 2026-08-07 — 总线驱动的 run 终于可以被停止

在此之前 `_invoke_runtime` 调 `run_and_collect` **不传 cancellation**,
runtime 于是自己造一个外界无法触发的 no-op token —— 这就是 2026-07-23
事故的机制底座:群聊里喊停,8 分钟内什么都不会发生,因为**从任何地方都
停不掉一个总线驱动的 run**。

三处改动:

- `_handle_channel_batch` 造 token,经 `on_event_id` 回调注册进
  [[cancel_watcher]](Step 0 铸出 event_id 的那一刻是最早能键的时机),
  用 `stack.callback` 保证无论怎么退出都 unregister。**所有** bus run 都
  注册,不只 team —— 端点是 run-scoped 的,未来 dashboard 也该能停一次
  peer-DM 轮。
- token 经 `cancellation=` 走 extra_kwargs 缝一路到 `AgentRuntime.run`
  (`collect_run` 的 docstring 早就把 `cancellation` 列为可透传参数),
  中间**没有任何签名需要改**。通道一直是通的,只是没人往里传。
- 新增 `except CancelledByUser` 分支,必须在通用 `except Exception` 之前。

## 2026-08-07 — 取消分支为什么必须自己 ack 游标

成功路径的 `ack_processed` 在 try 块**末尾**,异常会跳过它。所以取消若不
自己 ack,那条消息仍然 pending,下一轮轮询会把用户刚刚停掉的那个 run
**重新拉起来** —— 停止会表现为"它自己重启了"。这是取消分支里唯一"必须
发生且无处可依"的动作。

同时三件事**不能**发生(通用分支会全做):

1. `record_failure` —— 攒够 3 次进 poison,`get_pending_messages` 从此
   **永久**过滤掉这条消息。停三次就把一条消息弄成不可投递。
2. owner 面的永久失败通知 —— 用户自己按的停止,却收到"你的 agent 坏了"。
3. 下游失败告警 / 重试记账。

`import` 位置:`cancel_watcher` / `cancellation` 在 `_handle_channel_batch`
**方法体内** import,不在模块顶层 —— `agent_runtime` 会拉进 `module`,后者
又 import 回本包,顶层 import 直接循环(`get_agent_runtime_client` 一直
留在 `_invoke_runtime` 里就是同一个原因)。

## 2026-08-07 (二次) — team_id 经显式参数下传，不靠闭包

初版把 `"bus_team_id": team_id if is_team else ""` 直接写进 `_invoke_runtime` 的
`trigger_extra_data`，但那两个名字属于**调用方** `_handle_channel_batch` 的作用域 →
`NameError`，且 `_invoke_runtime` 是每个 bus turn 必经路径（等于团队回合全崩）。

现改为 `_invoke_runtime(..., team_id: str = "")` 显式参数，由调用方传
`team_id if is_team else ""`。

**该错误当时没被发现的原因值得记**：改了 `message_bus_trigger.py` 却只跑了
`tests/module/` 和 `tests/context_runtime/`。**改了哪个模块就跑哪个模块的测试**，
不能只跑「感觉相关」的那几个。

## 2026-08-07 — trigger_extra_data 新增 bus_team_id

team turn 的 `team_id` 本来就在 `is_team` 分支里算好了（用于 team prompt），只是没往下游
传。现在发布到 `trigger_extra_data`，供 [[context_runtime.py]] 注入 MCP 身份 header，让工具
从**服务端**而非模型参数得知本回合属于哪个 team。非 team 回合为 `""`。

不折叠进 `bus_errand_channel`：后者只在 errand continuation 时 stamp，多数 team turn 为空。

## 2026-08-04 — team 房标记进 trigger_extra_data（bus_team_room）

team 房与普通 bus 轮同为 working_source=MESSAGE_BUS，但交付契约相反
（前者纯文本自动上墙、prompt 禁投递工具；后者只有工具调用才送达）。
`_handle_channel_batch` 的 team 分支把 `team_room=is_team` 传入
`_invoke_runtime`，后者盖进 `trigger_extra_data["bus_team_room"]` →
`ctx_data.extra_data`，供 [[message_bus_module]] 的 expressive 声明门控
（team 房不广告 bus 工具，避免自动上墙 + 工具调用双发）。与
include_monologue 同形的两端钉死（tests/message_bus/test_team_room_marker.py）。

## 2026-08-03 — turn-source 章记录的是「轮次种类」,不是「这条消息在问还是在答」

Review round 3 抓到的复发:`sender_turn_source == "message_bus"` 被当成
「这是回复」的充分条件,但 bus 轮次里也能**提问**——Owner Relay 指令自己就
教发起方"有澄清问题用 bus_send_to_agent 追问"(路径 A),回答方也可能为了
组织答案再问第三个 agent C(路径 B)。两条路径上收件方都会误判成
Owner Relay,P1 原样复发。

修成两半:
1. **发送侧,按「这一条发给谁」盖章(不是按整轮)**:trigger 把分类器判定
   (`i_started`)转成本轮的**差事作用域**——`_invoke_runtime` 用
   `sender_agent_id`/`channel_id` 填
   `trigger_extra_data["bus_errand_peer" / "bus_errand_channel"]`,经
   [[context_runtime]] 落到 MCP identity header/bearer;工具侧
   `_send_turn_source`(见 [[_message_bus_mcp_tools]])把**本条 send 的目标**
   与作用域比对,只有打向差事对手的那条才盖 [[hook_schema]] 的
   `BUS_ERRAND_TURN_SOURCE`。
2. **收件侧兜底** `_i_have_errand_in_channel`:全批都是 plain
   "message_bus" 章时,只有当**我自己**在此 channel 有过非
   "message_bus" 章(或 legacy NULL)的发言——即我真的问过——才算
   「对我差事的回复」;从没问过的 agent 不可能被欠答案(路径 B:
   回答轮次 fan-out 给 C)。判据已下推进 SQL(`SELECT 1 … IS NULL OR <>
   … LIMIT 1`):这条查询跑在最常见的触发路径上,而 DM channel 被对称复用、
   永不新建,客户端全量扫描会随 agent 对的寿命单调增长。DB 失败 →
   Owner Relay,与外层同向降级。

### 为什么第 1 步必须 per-send —— 整轮盖章曾让 P1 换个位置复发

第一版把整轮盖成 `message_bus_errand`。但**一轮不只包含差事**:
`MessageBusModule.gather` 每轮调 `bus.get_unread`,而
[[local_bus]] 的实现是跨**所有** channel JOIN 成员表,把别的 channel 的未读
注进 `extra_data`;模块提示词紧接着**要求**回答它们(「A question is never
ping-pong — answer it」)。于是「A 在差事延续轮次里顺手回答了 C 在另一个
channel 的提问」是**平台自己注入 + 自己要求**的路径,不是角落:C 收到的回答
被盖成提问 → C 不再向自己 owner 回报 → 正是本 PR 要修的那个失败,换了个座位
(2026-08-03 review round 4)。只有 send 现场知道自己打给谁,所以只有 send
现场能定章。

### 已接受并写进 docstring 的残余洞(**不是**"风险已穷举")

1. **旧差事**:我曾在此 channel 跑过差事,之后同伴**从回答轮次**问我一个全新
   问题(无任何提示词引导这条路)→ 旧差事行仍投 Owner Relay 票。轮次章表达
   不了 per-message 的问/答意图。退化 = 修复前行为。
2. **同一 DM channel 里双向差事同时在飞**:DM channel 对称复用,若差事对手
   **也**问了我们什么、而我们在差事延续轮次里回答了他,这条回答打的正是差事
   作用域 → 盖成提问 → 对方去"回复同伴"而不向自己 owner 回报。要踩到得两边
   owner 同时各派了一个指向对方的差事。**这是我们主动选的方向**:另一条路
   (整轮盖章)破的是平台**自己引导**的场景(跨 channel 未读每轮注入 + 提示词
   要求回答),触发频率高得多。
3. **群 channel 当差事 channel**:作用域也按 channel 匹配,所以发进「恰好是
   差事 channel 的群」会把每个成员的那份都盖成提问。bus 差事跑在自动建的 DM
   channel 上,要踩到得手工建群并拿它当差事 channel。
4. **大小写/手写章**:差事行检查用 SQL `<>` 精确比对,非我方写入器写的、大小写
   不同的行会被算作差事行 → Owner Relay,与其它降级同向。

彻底关掉 1、2 需要**逐条声明意图**(发送方每条说明"这是问还是答")——review
提过、我们**没有采纳**:那会把一个正确性关键位重新压在模型听话上(铁律 #15:
机器可知的事实不能取决于用户选了哪个模型)。将来要做,默认值必须是**推导**
出来的,不能靠假设。

## 2026-08-01 — Owner Relay 只发给「发起方」,被问的一方改成「回复同伴」

P1 段 06 的**真正根因**,靠真机跑出来的(单测抓不到):`_build_prompt` 只要
`owner_user_id` 存在就追加 `## Owner Relay — REQUIRED`——而它总是存在。
于是**被问的那一方**也被告知"你的 owner 当初让你联系这个 peer,他正在
聊天里等答案"。对收件方这是**假话**:它的 owner 什么都没问。

现场后果(连跑 3 次复现):小雀替 TC 转达问题 → 羽书 收到假的 Owner Relay
→ 调 `send_message_to_user_directly` 回给自己 owner,并认定差事已了
(「未回复小雀 — 她是转发…按 Reply Discipline」)→ 小雀(已向用户承诺回报)
永远等不到回复。**模型是在照做,是 prompt 在骗它。**

修法(2026-08-04 定稿):按**消息上记录的事实**选指令,不再靠 channel 排序推断。

发送方在 `bus_send_to_agent` 时把**自己这一轮的种类**写到消息上
(`bus_messages.sender_turn_source`):owner 面的 turn(chat/job/…)=我在跑
差事、这条是**提问**;`message_bus` turn = 我本来就在答同伴、这条是**回复**。
触发侧读进来那批消息的这个字段即可,零历史查询。

**两次靠 channel 排序推断都错了**,记下来别再试:
1. 「我在这个 channel 说过话吗」—— 被问方回复一次之后就"说过话"了,**追问**
   会翻回 Owner Relay,bug 原样复发。
2. 「谁开的场」—— `send_to_agent` 找 DM channel 是**对称查找 + 复用**
   (local_bus:245),所以 A 一旦 DM 过 B,opener 永远是 A;此后 B 反向跑差事
   问 A 时**两边都判错**:A 拿到 Owner Relay(把 B 的问题转给自己 owner,
   P1 原样复现),B 收到回复时被告知"你 owner 没在等"(不回报)。**这不是
   罕见退化,是那一对 agent 的反向永久失效** —— 而且第 2 点里 B 那一步
   是我这次改动**引入的回归**(改之前它会拿到 Owner Relay 并正确回报)。
   review 抓出来的。

降级顺序:字段为空(存量行)且**我从没在此 channel 发过言** → 显然是
被问方;否则 → Owner Relay(2026-08-01 前的行为)。**注意这个降级分支本身
就是上面第 1 种错法**,所以它只能是兜底、不能是常态:第一版 turn source
只走显式 header 而 codex 不转发,codex 提问方于是恒走降级,追问从第 2 个
问题起就错 —— 已通过让 turn source 搭 bearer 修掉(见 [[_mcp_identity]])。
DB 异常同样回落 Owner Relay:错 relay 只是体验噪音,错误压掉 Owner Relay
会让 2026-06 那个静默失败复活。

`_build_prompt` 的 `i_started_this_exchange` 改成**关键字必填**:它决定给
agent 两条互相矛盾的指令里的哪一条,漏传不该静默继承 Owner Relay。

真机验证(第 4 次):羽书 改为在 bus 上回复并附状态,还自己诊断了前三次
「我之前三次都直接回复了 TC…但 TC 似乎没看到」;小雀 随后被触发并
「已将羽书的回复完整转达给 TC」,同时正确地没有再 ping-pong 回去。
整条链路(发问 → 对方答 → 回报用户)闭合。

## 2026-07-31 — _get_agent_owner 委托 AgentRepository.resolve_owner

实现收敛到 repository seam。2026-08-10 起契约随 resolve_owner 拆分:DB 异常在 repository 层就转成 None,外层 except→'' 那条路基本不再触发——别按「异常回 ''」推理(见顶部条目)。

## 2026-07-31 — team reply rows are stamped with their turn's event_id

`_invoke_runtime` now returns `(response_text, event_id)` (from
`RunCollection.event_id`; None if the run died before Step 0 — including the
error-string path, which still carries whatever id Step 0 produced). The team
branch passes it to `bus.send_message(event_id=...)` so every posted reply
row references the turn that produced it — the per-MESSAGE handle behind the
transcript's reasoning disclosure, complementing `note_event_id`'s
per-MEMBER latest-turn binding on the activity row.

## 2026-07-30 — 只有 team room 分支对 collect_run 开 `include_monologue`

team room 的 prompt（`_build_team_prompt`）明说「你的明文会自动上墙」，所以
NexusPower 独白在这条分支并入收集文本（`include_monologue=is_team`）；peer
DM→收件箱分支的 prompt 让 agent 用 `send_message_to_user_directly` 送达、
从未承诺明文落库，独白保持私密（否则 owner 会同时收到润色直发 + 一条原始
独白的收件箱条目）。语义见 [[run_collector]] 同日条目。
（**2026-08-30 宪法改口为「不投递」**，见 [[library]] / 本文件 08-31 条目；此决定的判据不变。）

## 2026-07-28 — the poll loop stops being a single point of failure, and reports work

Two defects, one incident. Between 2026-07-27 00:17 and 2026-07-28 09:06 the bus
processed **zero** messages for **every** user — 33 hours — with no exception, no
restart, and a liveness signal that read healthy throughout. A container restart
drained the backlog in 0.1 s.

**Why it froze.** `_poll_cycle` did `asyncio.gather` over every agent that was a
member of any channel (364 on prod) and awaited all of them. Inside,
`_process_agent` takes one of `MAX_WORKERS` (3) semaphore slots and calls
`_invoke_runtime`, which by design has no timeout (binding rule #14). So three
wedged provider connections exhaust the pool, the gather never returns, and the
loop stops — for everyone, not just those three agents.

The cycle now **dispatches and moves on**: `_dispatch` spawns a supervised task
per agent (`_InFlight`, paired `add_done_callback` per incident lesson #2) and
the loop immediately continues. A stuck turn holds its own task and its own slot;
the loop keeps cycling and can still serve everyone else.

**Why nobody noticed.** This was the only long-running worker without its own
`ServiceAuditor`. The supervisor's `bus: running` is set once at start and never
updated — L1, not L2 (see [[run_worker_supervisor]], corrected in the same
change). Now `ServiceAuditor("message_bus_trigger")` emits started/stopped/error
plus a heartbeat carrying `liveness_snapshot()`, whose whole job is to make the
two failure modes distinguishable in SQL:

| symptom in `service_audit` | meaning |
|---|---|
| `cycles` frozen | the loop itself is wedged |
| `cycles` rising, `dispatched_total` frozen, `candidates` > 0 | loop fine, nothing can start |
| `running == max_workers` and `waiting` > 0, sustained | the worker pool is the bottleneck |
| `longest_running_agent` / `_s` | *who* is holding a slot |

`longest_running_*` is **diagnostic only**. Nothing here force-stops a turn: a
multi-hour run is a legitimate workload, and the fault being guarded is our loop
dying, not an agent taking its time (binding rule #14).

**Scan cost.** `_agents_with_pending()` replaces "every channel member" with one
query for agents that actually have a message past their cursor. Deliberately
over-inclusive: it skips the @mention filter because an un-addressed member is
precisely who must be dispatched so `_process_agent` can ack and advance its
cursor — filter them here and cursors freeze and the scan never converges.

`stop()` also sets an event so the loop leaves its interval sleep at once instead
of waiting out up to `POLL_MAX_INTERVAL`, and cancels in-flight dispatches so the
loop that owns them doesn't leak them.

## 2026-07-30 — team turns bind their event_id onto the activity row

The team branch also hands `act.note_event_id` to `_invoke_runtime` as
`on_event_id`; `collect_run` fires it once when the Step-0 progress message
surfaces the turn's events-row id. Non-team invocations pass nothing — the
parameter defaults to None end to end.

## 2026-07-28 — team activity scoped by `turn()`

The team branch's three-part activity dance (mark_running up front, a bespoke
throttled `_make_activity_progress` closure, mark_idle in a `finally` wrapped
around only the runtime call) collapsed into
`async with _bus_activity.turn(...) as act` over an `AsyncExitStack`, with
`act.on_progress` handed to the runtime. The scope now covers the whole
handled batch rather than just `_invoke_runtime`, and the timer heartbeat that
keeps the row live during a silent stretch belongs to `turn()` — see
[[_bus_activity]]. `_make_activity_progress` is gone.

`POISON_FAILURE_THRESHOLD` is now imported from [[local_bus]] instead of being
a hand-synced copy.

## 2026-07-22 — no longer its own OS process; runs under the worker supervisor

`MessageBusTrigger.start()` / `_get_bus()` are unchanged, but the trigger is no
longer launched as a standalone `-m ...message_bus_trigger` process. It is now
one supervised task inside [[run_worker_supervisor.py]] (shared event loop + DB
pool). Two consequences worth noting: (1) its flag-based sync `stop()` means the
`while self._running` loop exits at the next poll boundary (≤ `POLL_MAX_INTERVAL`
12 s) — the supervisor's cancel is the backstop; (2) it has no `ServiceAuditor`
of its own, so the supervisor's per-worker liveness snapshot (state `bus:
running/restarting`) is its FIRST L2 signal. The "独立进程" framing below is
HISTORY; `__main__` is retained as a debug entrypoint.

> **Both numbered points above were superseded on 2026-07-28 — see the entry at
> the top of this file.** (1) `stop()` now wakes the loop immediately; (2) the
> supervisor snapshot was never L2 — it is L1, and it is exactly what let a
> 33-hour outage look healthy.

## 2026-07-22 — team prompt: "room files are already shared" note

Added an intro line stating every member already sees every message/file posted in THIS room
(it's in the scrollback), so there's nothing to "forward" and no claiming you did. Kills the
cosmetic "I forwarded it ✅" white lie an agent emitted when relaying — @mention is enough,
the teammate sees the same room.

## 2026-07-22 — team rule: reply-delivery forbidden, action tools allowed

Refined the group-chat tool rule again. It now distinguishes REPLY-DELIVERY functions
(forbidden — the text reply auto-posts, so `send_message_to_user_directly` /
`bus_send_message` / `bus_send_to_agent` would double-deliver) from ACTION tools (allowed):
`Read` opens a file, and **`bus_share_to_team`** publishes a file the agent produced to the
team folder (it stages bytes, does NOT post a message — the agent then mentions the returned
path in its reply). The prior blanket "no send/bus" ban blocked "share this file with the
team" and led an agent to fake a "forwarded ✅" it couldn't perform.

## 2026-07-22 — team prompt feeds recent room history (not just the @mention)

`_build_team_prompt` now takes `history` (recent scrollback via
`LocalMessageBus.get_recent_messages`, `TEAM_HISTORY_LIMIT=20`, oldest→newest) plus
`trigger_messages` (the @mentions for this agent). Before, a triggered agent only saw the
messages that @mentioned IT — so when the user posted an image @agent_1 and asked it to
relay to @agent_2, agent_2 never saw the image and the relay dissolved into a
"forward it again" back-and-forth (agent_1 even hallucinated a successful forward). Now any
triggered agent sees files/images posted by anyone in the room and Reads them directly; the
prompt points it at the latest @mention to answer. No manual relay / bus_share_to_team needed
for "discuss a shared file". `_handle_channel_batch` fetches the history in the team branch;
the retrieval anchor still uses the @mention batch only.

## 2026-07-21 — team group-chat rule: allow Read, forbid only send/bus

`_build_team_prompt`'s reply-only rule used to say "Do NOT use any tools", which made an
agent REFUSE to open a shared image/doc it was asked about (either from a `[Shared file …]`
marker or a path a teammate pasted into text). "Reply-only" is meant to prevent re-sending /
triggering teammates, NOT to block reading a file. Rule generalized: forbid
send/bus/@-trigger-to-deliver, but explicitly ALLOW read-only tools (esp. the built-in Read)
to open a file path, then reply in plain text. Applies whether or not the message carries a
structured attachment — the path often arrives as plain text.

## 2026-07-20 — prompt builders inject attachment markers + team shared-folder hint

Both `_build_prompt` (DM/owner-relay) and `_build_team_prompt` now append
`build_bus_markers(msg.attachments, …)` after each message body, so a file sent
over the bus surfaces to the recipient as the same `… use Read tool …` marker a
user upload would (see [[_bus_attachment_impl]]). `_build_team_prompt` gained
`owner_user_id` / `team_id` params (derived at the call site: owner via
`_get_agent_owner`, team_id from `channel_owner[len("team_"):]`) and, when known,
prints the team's shared-folder path (`team_shared_dir`) so teammates know where
`bus_share_to_team` drops land. Markers need no per-recipient resolution — the
stored rel_path is rebuilt against `base_working_path` into an absolute path.

## 2026-07-13 — Agent 实时层熔断器接入

`_process_agent` 顶部（信号量之前）加熔断器 `should_skip` 闸门：paused/cooling 的 agent 整体跳过，且**不消费**其 pending 消息（不 ack，留队待恢复）。这是让 bus 停止重触发坏 agent 的关键。

## 2026-07-03 — IM-channel skip prefixes now registry-driven (wechat double-dispatch)

The hand-maintained `_IM_CHANNEL_PREFIXES = ("lark_", "telegram_", "slack_")`
tuple silently drifted: wechat / narramessenger / discord were missing, so
every message on those channels was re-dispatched from their ChannelInboxWriter
history rows — a SECOND AgentRuntime run wearing the Owner-Relay peer-agent
prompt (2026-07-03 dev incident: the second run fabricated a wechat_send
context_token and sent "我已经在微信上回复你啦" platform DMs; ~$0.22 wasted
per message). New module-level `im_channel_prefixes()` derives the skip set
from `MessageSourceHandler.dedicated_trigger` registrations at call time
(import-order safe). Guarded by tests/message_bus/test_bus_channel_inbox_skip.py
(filesystem truth: every run_*_trigger.py must have a dedicated handler).

## 2026-07-02 (PR #45 review follow-up) — cooldown arms after write, error is redacted

Two fixes from automated PR review on the failure-notification change below:

1. **Cooldown timing**: `_failure_notify_cooldown[cooldown_key] = now` moved
   from *before* the `try` block to *after* `InboxRepository.create_message`
   succeeds. Previously, arming the cooldown up-front meant a transient
   inbox-write failure (DB blip, etc.) silently suppressed the real
   notification for the next `FAILURE_NOTIFY_COOLDOWN_SECONDS` — the owner
   would get NOTHING for 30 minutes even though nothing was ever written.
2. **Secret redaction**: new `_redact_error_for_owner` (static method) masks
   `sk-...`-style keys, `key=value`/`token=value` pairs, and `Bearer ...`
   headers, then truncates to `MAX_NOTIFIED_ERROR_LEN` (500 chars), before
   the error is embedded in the inbox `content`. Provider SDKs routinely
   echo the credential back in the error body (OpenAI: "Incorrect API key
   provided: sk-..."), so `str(exception)` was never safe to show verbatim
   to the owner. `_classify_error` still runs on the RAW (unredacted) error
   — it only pattern-matches keywords for the hint/cooldown category, never
   displays the string, so there's nothing to redact there.

## 2026-07-02 — permanent-failure notification (fixes NetMindAI-Open/NarraNexus#52)

`_handle_channel_batch`'s `except` block now checks the failure count right
after `record_failure()`. Once it reaches `POISON_FAILURE_THRESHOLD` (3, kept
in sync with `LocalMessageBus.get_pending_messages`'s inline `failure_count <
3` filter — see `local_bus.py.md`), `_notify_permanent_failure` writes an
`InboxMessageType.SYSTEM_NOTICE` row via the same `InboxRepository` path
`_write_to_inbox` already uses (fresh `get_db_client()`, not `self._bus._db`
— `LocalMessageBus` only holds the raw backend). Before this, a message that
hit the poison threshold just vanished from `get_pending_messages` forever
with zero owner-facing signal — the exact silent-failure bug reported in
NetMindAI-Open/NarraNexus#52 (broken OpenAI provider → every IM/bus message
dropped after 3 failed `_invoke_runtime` calls, no visibility, no recovery).

De-duplicated per `f"{agent_id}:{error_category}"` with a 30-minute cooldown
(`_failure_notify_cooldown`, same in-memory / per-process pattern as
`_rate_counters` — resets on restart, an accepted tradeoff) so a batch of
messages failing for one root cause (e.g. every pending message for an agent
whose provider key just broke) writes at most one inbox row, not one per
message. `_classify_error` does a coarse substring match on the stringified
error for `"credential"` / `"api_key"` / `"401"` / `"provider"` / etc.
markers — this only changes the hint text ("check the agent's LLM provider
configuration…" vs. a generic "check recent activity"), not any retry or
delivery behavior. The recovery half — clearing a failure record so
`get_pending_messages` picks the message back up — lives in
`backend/routes/agents/bus_failures.py`, not in this file (this file only
detects + reports the permanent failure).

## 2026-06-23 (PM) — prompt names the live roster, forbids off-channel @mentions

`_build_team_prompt` now states the current channel members explicitly and adds
a rule: only @mention someone in that list; anyone named in history but not a
member has left / was never here. Fixes agents @mentioning a non-member (e.g.
Nex @rabbit when rabbit isn't in the channel). Delivery was already safe
(`_extract_team_mentions` only resolves to real members) — this stops the agent
from *writing* the dead mention in the first place.

## 2026-06-23 — team group-chat branch + cascade cap + faster polling + cursor fix

`_handle_channel_batch` now branches on `channel_owner.startswith("team_")` (a
team group-chat room — see `teams.py.md`). **Team branch**: a group-chat prompt
(`_build_team_prompt`) that forbids tools / process-narration and just talks; the
agent's plain reply is posted BACK into the channel as that agent, with
@mentions parsed (`_extract_team_mentions`, @Name/@all → member ids / `@everyone`)
so a hand-off pulls teammates in. Every non-team channel (peer DM, IM bridges)
keeps the original owner-relay + inbox path untouched. **Cascade cap**:
`_team_cascade_depth` counts consecutive trailing agent (non-`usr_`) messages;
past `MAX_TEAM_AGENT_HOPS` (4) the reply's @mentions are dropped so two agents
can't @ each other forever (a human message resets the chain). **Latency**:
adaptive poll bounds lowered to MIN 3s / MAX 12s (was 10/120) so a reply lands
quickly after idle.

Bug fix (shared, all bus delivery): the cursor-advance calls used
`str(latest.created_at)`. When `created_at` is an auto-parsed `datetime`, `str()`
gives space-format `"YYYY-MM-DD HH:MM:SS+00:00"` while `created_at` is isoformat
`"…T…+00:00"`; lexicographic compare in `get_pending_messages` ('T' > ' ') then
makes every newer message look unprocessed → the agent loops. Dropped the
`str()` wraps; canonicalisation now lives in `local_bus.ack_processed`.

## 2026-06-12 — owner-relay prompt names the owner; routing keeps the user_id

`_build_prompt` gained an `owner_name=""` param. The human-facing relay line now
reads `Your owner **{owner_name or owner_user_id}** originally asked…` so the LLM
sees the owner's human name, not the opaque NetMind userSystemCode. The
`send_message_to_user_directly` routing argument on the same prompt KEEPS
`user_id="{owner_user_id}"` verbatim — the delivery tool needs the real key, so
that hex must stay. The caller resolves `owner_name` via
`UserRepository(await get_db_client()).get_display_name(owner_user_id)` (see
[[user_repository.py]]).

## 2026-06-09 — `_get_channel_info` SQL dialect bug (silent bus-delivery break)

`_get_channel_info` queried `bus_channels` with a MySQL `%s` placeholder via the
RAW backend `self._bus._db.execute(...)`. `_get_bus()` hands LocalMessageBus
`db._backend` (NOT the AsyncDatabaseClient wrapper), so the wrapper's `%s`→`?`
dialect translation never ran — SQLite threw `near "%": syntax error` on EVERY
poll cycle for any agent that had channel messages, aborting `_process_agent`
before delivery. **Symptom**: agents that were sent bus messages silently never
received them (2026-06-09: 零 created 影/镜 and messaged them; they stayed mute —
0 events — until this fix, then both processed the message and replied). Fixed
by routing through the dialect-aware `self._bus._db.get_one("bus_channels",
{...})`. Lesson: raw `backend.execute` takes SQL verbatim; only the
AsyncDatabaseClient wrapper translates dialects — never hand-write `%s` on a
path that holds a raw backend. Regression:
`tests/message_bus/test_channel_info_dialect.py` (constructs the bus with the
RAW backend to mirror production, else the wrapper hides the bug).

## 2026-05-19 — `_write_to_inbox` routed through `InboxRepository`

The hand-written `db.insert("inbox_table", ...)` referenced an `agent_id`
column that doesn't exist in `inbox_table` and an `owner_user_id` field
where the schema has `user_id`, and omitted the required `message_id`.
EC2 bus container surfaced `Unknown column 'agent_id' in 'field list'`
13 times in 3 hours on 2026-05-18.

Now we delegate to `InboxRepository.create_message` (the canonical
writer), generate a `bus_<uuid12>` message_id, and tag the row with a
new `InboxMessageType.MESSAGE_BUS` enum value. `MessageSource` is set
to `type="message_bus"`, `id=channel_id` so the inbox row traces back
to its origin channel. The previous JSON blob with original message
preview was dropped — that diagnostic data lives in `bus_messages`
already; the inbox row is a notification, not an audit copy.

## 2026-04-20 — runtime consumption via `collect_run` (Bug 2)

`_invoke_agent_runtime` now uses `collect_run`. When
`collection.is_error` is true it returns a structured `"⚠️ I couldn't
process your message right now (error_type). error_message"` string so
the sender agent sees the failure inline instead of receiving an empty
reply.

> **2026-08-14 更新**(取代 08-13 那条,它描述的三元组从未合入):返回值是
> `TurnResult`,`fatal` 字段说明"这是错误串而不是 agent 的话"。
> DM lane 的行为不变;team lane 的投递已搬进 turn,所以那条 ⚠️ 由 team 分支
> **以房间身份**单独贴出(详见同日条目),而不再经由这里的返回值被当成回复贴进房间。

## 2026-05-12 — IM channel skip extended to telegram_ / slack_

`_process_agent()` already skipped `lark_` channels (written by `ChannelInboxWriter`
for frontend Inbox display). The same skip was missing for `telegram_` and `slack_`,
causing `MessageBusTrigger` to re-consume those messages and fire `AgentRuntime` a
second time — producing duplicate replies to the IM sender. Fixed by checking all
three prefixes together via `channel_id.startswith(("lark_", "telegram_", "slack_"))`.

# message_bus_trigger.py — MessageBus 事件驱动轮询引擎

## 为什么存在

Agent 收到消息后不能靠自己去轮询——它不知道什么时候有消息，也无法保持长连接。`MessageBusTrigger` 是代替 Agent 做轮询的"邮差"：它扫描所有频道成员、找出有待处理消息的 Agent、把消息批量投递给 AgentRuntime 处理、更新投递游标。

它替换了之前的 `MatrixTrigger`（Matrix 专用轮询），成为所有 Agent 间消息的统一投递机制。

## 上下游关系

**被谁启动**：独立进程，`uv run python -m narranexus.platform.message_bus.message_bus_trigger` 或 `python -c "import asyncio; from narranexus.platform.message_bus.message_bus_trigger import main; asyncio.run(main())"` 启动；Makefile 里应有对应的 `dev-message-bus` 命令（或集成到 `dev-poller`）。

**调用谁**：
- `LocalMessageBus.get_pending_messages()` 取待处理消息
- `AgentRuntime.run()` 处理消息（通过 `_invoke_runtime()`）
- `LocalMessageBus.ack_processed()` 推进游标（成功后）
- `LocalMessageBus.record_failure()` 记录失败（失败后）
- `db.insert("inbox_table", ...)` 把 Agent 的回复写入用户 inbox（通过 `_write_to_inbox()`）
- `InboxRepository.create_message()`（`message_type=SYSTEM_NOTICE`）把永久失败通知写入 owner 的 inbox（通过 `_notify_permanent_failure()`，当某条消息的失败次数达到 `POISON_FAILURE_THRESHOLD` 时触发；见下方 2026-07-02 changelog）。这个失败记录的读取/清除（重试恢复路径）在 `backend/routes/agents/bus_failures.py` 里，**不在**本文件——本文件只负责检测和上报。

## 设计决策

**自适应轮询间隔**：有消息时 `current_interval` 降到 `POLL_MIN_INTERVAL=10s`（快速处理积压），无消息时每次增加 `POLL_STEP_UP=15s`，最大到 `POLL_MAX_INTERVAL=120s`（减少空转）。这比固定间隔更高效。

**Rate Limiting**：同一 Agent 在同一频道 30 分钟内最多被激活 20 次（`RATE_LIMIT_MAX=20`, `RATE_LIMIT_WINDOW=1800s`）。超限时跳过处理但仍推进游标（消息被"丢弃"而非积压）。这防止了高频消息导致 Agent 被无限触发。

**Mention 过滤**（见 `_should_process_message()`）：频道 owner 总是被激活；非 owner 只有被 @mention 时才激活；任何人不处理自己发的消息。这三条规则是防止 Agent 间触发死循环的核心。

**并发控制**：`asyncio.Semaphore(max_workers)` 限制同时处理的 Agent 数量（默认 3），防止多个 AgentRuntime 并发运行消耗过多资源。

消息被组织成 per-channel 批次（`by_channel: Dict[str, List[BusMessage]]`），每个 channel 的消息一起投递，LLM 看到的是完整的上下文而不是碎片化的单条消息。

## Gotcha / 边界情况

`_get_bus()` 函数的注释说"LocalMessageBus is a misnomer"——它其实支持任何后端（SQLite 和 MySQL），不仅仅是本地。这个名字是历史遗留，未来可能重命名。

`_write_to_inbox()` 在 AgentRuntime 处理成功后把 Agent 回复写入 inbox——如果 Agent 的回复是空字符串（`final_output` 为空），不写入 inbox。但 `ack_processed()` 仍然会被调用，消息游标依然推进。这意味着 Agent 选择"沉默"（不回复）和"处理失败"（抛异常）在游标层面的效果是不同的：沉默会推进游标，失败会 `record_failure()`。

Rate limiter 的计数器用的是 `time.monotonic()`（进程内单调时钟），重启进程后计数器清零。如果进程崩溃后立即重启，30 分钟限额会重置，可能导致一批消息被重新处理。

## 新人易踩的坑

`_invoke_runtime()` 把所有 pending 消息组成一个 prompt（`_build_prompt(messages)`）传给 AgentRuntime，不是一条一条单独处理。这意味着 AgentRuntime 一次性看到所有积压的消息，LLM 的处理代价随消息数量线性增加。如果积压了 50 条消息，这一次 AgentRuntime 调用的 token 使用量会很高。

`trigger_extra_data={"bus_channel_id": channel_id}` 是通过 AgentRuntime 传递频道信息的方式。如果 AgentRuntime 步骤里有读取 `trigger_extra_data` 的逻辑，需要知道 key 是 `"bus_channel_id"`。

## 2026-07-07 — 凭据分类 + 脱敏抽到 agent_framework/llm/failure

`_classify_error` / `_redact_error_for_owner` 现委托到共享的
`agent_framework.llm.failure`（`is_credential_error` / `redact_secrets`）。行为不变
（`MAX_NOTIFIED_ERROR_LEN` 仍 500），只是让 bus / narrative / Step-5 hooks 三条后台
路径用同一套判断（去重，铁律 #8）。原本散落此处的 markers / _SECRET_* 正则已移除。

## 2026-07-22 — team runs mirror live activity

The team branch of `_handle_channel_batch` wraps the run: `mark_running` before, an opt-in
`on_progress` (via `_make_activity_progress`, throttled — writes on phase change or ~2s
heartbeat) passed through `_invoke_runtime`→`run_and_collect`→`collect_run`, and `mark_idle`
in a `finally`. Populates [[_bus_activity]] so the team UI shows running/phase/elapsed. Only
team channels; DM/IM/Job paths pass `on_progress=None` (unchanged).

## 2026-08-05 — [bus-timing]：每 hop 一条计时线

与 runtime 的 [turn-timing] 配套：`_handle_channel_batch` 成功路径落
`[bus-timing] agent= channel= team= batch= queue_wait_s= turn_s= hop_s=`。
queue_wait=消息入库→本次 dispatch（受自适应轮询 3-12s 约束）,turn=runtime
调用,hop=入库→送达完成（team 房含 post 回房;DM 的 bus_send 在 turn 内,
turn 即覆盖）。created_at 解析复用 run_recorder.parse_db_utc（datetime/ISO
字符串都吃,缺失回落 -1.0 不炸）。失败路径不发计时线。
测试:tests/message_bus/test_bus_hop_timing.py。

### 2026-08-05 R2（review 修正）：解析器归一 + hop 语义一致 + 观测不进 try

- 时间戳解析改用**包内已有**的 `local_bus._as_utc`（同一张表同一字段两个
  解析器是下次改语义只改一边的入口）,删掉对 agent_runtime.run_recorder 的
  跨包依赖。
- created_at 缺失时 `hop_s` 同发 -1.0（R1 会静默换定义成 dispatch→delivered,
  混进 p50/p99 把分布拉低）;一个过滤条件摘掉全部不完整行。
- 新增 `oldest_wait_s`：queue_wait 量的是**触发消息**（批次里最新一条）,是
  用户等待的下界;oldest 是上界。batch>1 时两者并读。
- `[bus-timing]` 行移出 try——观测代码不该有能力把已送达已 ack 的消息记成
  投递失败并推进毒药计数。成功（_hop_done）才发。

### 2026-08-05 R3：deliver 差值语义回写

hop_s − queue_wait_s − turn_s = 投递段（runtime 返回后的 ack + 上墙/写
inbox）——是有意义的第四个量,不是误差（R2 重写注释时丢了这句,review 指出,
已回写进代码注释）。

## 2026-08-11 — 公告栏注入 `_build_team_prompt`

补上 PRD 说的那个空位：team 级别「持久 × 共享 × 每轮必然载入」的状态。这里是唯一注入点，
`:640` 是每个 team turn 的必经之路。

**位置是有承载的**：公告栏排在 roster / 共享文件夹之后、scrollback **之前**。
它是这些消息被阅读时所处的约束框架；追加在二十行聊天之后，它读起来会变成聊天的脚注。

**空公告栏一个字符都不加**（连标题都不加）。有测试断言 prompt 与加功能前**逐字节相同**，
所以从不使用这个功能的团队分文不付。

**读取失败降级，不拖垮 turn**：丢掉常驻规则是降级，丢掉回复是故障。抽成 `_load_bulletin`
是为了让这个降级**可测**，而不是埋在 dispatch 路径里；两条分支都有测试，
并且经变异验证——摘掉 guard 会立刻变红。返回 `[]` 但**必须记 warning**：
静默会把「数据库连不上」呈现成「这个团队没有规则」。

同时在 prompt 里点名 `bus_pin_team_rule`（没人告知的工具就是没人用的工具），
并在同一句里劝阻把公告栏当记事本——预算很小且与用户的规则共享，
一个往里钉「发现」的 agent 会挤掉它本该遵守的规则。

## 2026-08-10 — 巡查 lane 补齐:身份、闸门、事实与说话权的先后

**巡查轮次现在和消息轮次一样开 `_bus_activity.turn`**。上线时漏掉了,后果不是
「少个 UI 指示」而是这条主线上工作板工具全废:`_work_board_mcp_tools` 当时只从
`bus_agent_activity` 认房间,巡查不写这张表,于是 5 个工具在**平台自己叫 lead
调 `work_complete_item` 的那一轮**统一返回 no room / not found。连带两处:
roster 整个 sweep 期间显示 lead 空闲。

**开这行**不能**顺带解决自我 stall**——这点最初写错了,更正在此:活动行是
per-agent 单行,描述的是**当前这一轮**,而巡查者当前这一轮就是巡查本身。检测在
开行之前读到 idle(于是 lead 每轮把自己的条目标 stalled,永不恢复,prompt 还会
让 lead 去催自己),开行之后读到 running(于是 lead 的条目永远不会 stalled)。
两个读数都不携带「这个条目有没有在推进」的信息。所以真正的修法是
`detect_stalled_items(executor_agent_id=...)` **把巡查者自己的条目跳过** ——
代价是「lead 卡在自己条目上时,**没有任何人**会发现」—— 巡查每个 team 只有一
条、由该 team 的 lead 跑,不存在第二个 sweep 兜底(详见 [[patrol]] 的更正)。
明写接受,因为另一个选项是完全没有恢复路径的永久自我催办。

**同时给 `_invoke_runtime` 传 `team_id=`**,MCP 身份头才有 team 可注入。这与上一
条是同一个洞的两头:工具必须从服务端知道自己在哪个 team,不能从模型参数知道。
resolver 那侧也改成**优先读注入身份**、activity 行退化为兜底 —— 见
`_work_board_mcp_tools.py.md`,那份文档原本就写下了「依赖 activity 写入时机」
这个风险预判,巡查 lane 正是把它兑现的那个分支。

**熔断门**:`_dispatch_patrols` 在唤醒 lead 前过 `should_skip`,和消息派发同一道
闸。没有它,一个 key 失效或额度耗尽的 lead 会被每 180–600s 唤醒一次去跑一轮
注定失败的 turn —— 正是熔断器要掐断的循环,只是从一条没问过它的 lane 进来。
外层不包 try/except:`should_skip` 自己 fail-open,包了就是个永远跑不到的
handler,而死 handler 会让读者以为它会抛。

**`flight.running` 置位**:`liveness_snapshot` 的饥饿检查和 `longest_running_agent`
都只数 `running`,不置位的巡查会占着 worker 槽却在心跳里显示为「仅在等待」——
2026-07-27 那种「33 小时什么都没发生,liveness 全绿」的形状。

**事实采集先于说话权**:`detect_stalled_items` 挪到频控门之前。检测不是「说话」
的一部分 —— 它把 `stalled` 写进板子,那是用户面板渲染的东西,也是下一次 sweep
定速的依据。频控一并挡住它时,被 cap 的团队整个窗口都不刷新板子:窗口内变哑的
条目事后仍显示 `in_progress`,UI 少报,自适应间隔还停在慢档 —— 恰恰在出问题的
时候。一次读加一次状态写,和频控真正要省的那轮 LLM 不在一个量级。

## 2026-08-11 — sweep 是一次完整的 run,不是「顺带开一行」

上一轮只把活动行开在 `_invoke_runtime` 外面,开了行却不填它。三个后果,严重度
递减:

**(1) 用户可见回退,而且是本 PR 自己造的。** `bus_agent_activity` 是
per (agent, channel) 单行,`start()` 往 `event_id` 写 NULL。一次 sweep 之后 lead
那行的 `event_id` 被清空、再没写回,而 `_member_activity` 的 idle 分支正是把这
一列交给前端当**事件日志入口**。为了 roster 去开这行,结果把 roster 要的那个
链接弄丢了。

**(2) 巡查 run 停不掉。** 消息 lane 建 `CancellationToken` + `watcher.register`,
巡查没有,于是 owner 的停止对巡查 run 不生效,`_leave_room_trace` 按
`bus_agent_activity.event_id` 反查房间也落不到。

**(3) 巡查自己不可观测**:sweep 期间 roster 显示 running,点进去没有 event_log。

现在照消息 lane 的完整形状补齐:`on_event_id` 里同时 `watcher.register` 和
`act.note_event_id`,`stack.callback` 反注册。活动行也从「只包 LLM 调用」改成
**包住整个 sweep**——检测开始那一刻 lead 就被占用了,roster 该如实说这段时间。

顺带把 sweep 主体拆成 `_patrol_body`:`_run_patrol` 现在只负责 activity/取消的
作用域 + 「无论如何都推进游标」这条不变量,主体在里面平铺,不用为了一个
`async with` 再缩进一层。

## 2026-08-11 — 活动行推迟到「真要跑 turn」才开

上一条把活动行提到 sweep 开头,理由是「检测开始那一刻 lead 就被占用了」。这个
理由**在同一次改动之后就不成立了**:正因为 `detect_stalled_items` 现在显式跳过
巡查者,检测阶段这行是开是关对判定毫无影响;工作板工具的兜底解析也只在
`_invoke_runtime` 期间才有人读。

而代价是实的:`start()` 写 `event_id: None` 并重置 `steps` / `started_at`,写回
要等 `on_event_id`。开得早,则**任何在跑 turn 之前返回的 sweep**(频控门命中,
或组装 prompt 时抛异常)都会顺路把 lead 上一次真实回复的事件日志入口抹掉 ——
正是本 lane 已经修过一次的那条用户可见回退,从另一扇门进来。而且不是边角:
板上有 stalled 时节奏 180s、30 分钟窗口约 10 次 sweep,而 `PATROL_SPEECH_MAX`
是 6,窗口尾部那几次全走这条空跑路径。

所以现在开在 `_invoke_runtime` 之前一行。roster 少显示前两次 DB 读那一瞬的
running,换回一个不会被抹掉的链接。

## 2026-08-11 — team 房间「跑过一轮 = 已读」

`last_read_at` 在 team 房间**从来没有推进过**。它唯一的写入方以「trace 里出现 bus
投递工具帧」为条件,而 team 回复是本 trigger 服务端代发的 —— 没有工具调用。于是游标
停在 `joined_at`,该 agent 有生之年每一条团队消息都算未读;而未读又以每轮 20 条的
上限注入它**所有场景**的上下文,owner 私聊也不例外。

新增 `_ack_room_seen`,挂在 `_handle_channel_batch` 的成功与被取消两条路径上。

**判据是"有没有跑 turn",不是"有没有回复"。** team 房间靠**渲染**投递:
`_build_team_prompt` 把房间 scrollback 写进 turn 的 user message,所以已经呈现给
模型的**就是那个窗口**。回不回复是 Reply Discipline 的决定,不是"我没看"。被取消
同理 —— prompt 早就构建完了,停止不能把已经展示过的东西变回没展示。

**但游标不能跑过窗口。** 初版把游标一路推到触发消息,等于宣告"该房间里 ≤ 触发消息
的一切都已展示";而 prompt 只渲染 `TEAM_HISTORY_LIMIT` 条。一个长期没被 @ 的成员
积压 60 条,某轮终于被 @ 时只看到 20 条,剩下 40 条**从未展示却被一起标成已读** ——
正是本方法在另外两个 ack 点明确拒绝造成的那种静默丢失,换到了会跑 turn 的这条路径上。

单根高水位游标表达不了「读了窗口但没读窗口下面那段」,所以判定是:
**未读集里只要还有东西早于本轮渲染的最老一条,游标就原地不动。**`get_unread` 本身
已经按游标过滤过,所以"它返回的、且早于窗口的"按定义就是本轮没渲染的 —— 一个问题,
有没有游标都成立。积压装得下窗口的房间(常态)仍然一轮收敛;装不下的那种,正是
"没被 @ 的成员靠未读瞥一眼"该继续生效的场景,而用户现在也有了可达的手动复位。

**刻意不挂在 `_process_agent` 的另外两个 ack 点上**(未被 @、被限流)。那两处推进
`last_processed_at` 但**没有跑 turn**,什么都没渲染、什么都没被看见。把它们标成已读
等于让消息未读先亡 —— 而且会一并砍掉「没被 @ 的成员靠未读列表瞥一眼房间动静」这个
能力,那是能力,不是疏漏。

**仅限 team 房间。** 在 DM 里未读列表**就是**队列:「我等下处理」依赖消息重新浮现,
所以只有真的回复才清账,那条路走模块钩子。

## 2026-08-11 — 与工作板并存：两块常驻内容的顺序

#259（Leader 巡查 + 工作板）先合入 dev，所以这次由公告栏来解这个我提前标出的语义重叠。

`_build_team_prompt` 现在有**两块**常驻内容，都排在 scrollback 之前，顺序是：

1. **公告栏** —— 规则，管「怎么答」；
2. **工作板 + Leader 职责** —— 状态，管「有什么没做完」。

规则在前，因为它是后面一切（包括工作板）被阅读时所处的框架。dev 那一块**一字未改**：
它刚合入，在一次 merge 里改写别人已发布的设计不合适。

**合并后的总量问题，以及它的处置。** 公告栏有硬顶（条目 2000 字 + 总结 800 字）且空态零字符；
工作板在渲染处没有上限，空态仍输出一行。两块相加没有总预算，而 20 条 scrollback 还要挤在同一个
prompt 里；一个积压 80 条的团队，每个成员每一轮都要付 80 行。

**结论：不给工作板加渲染上限。** 截断会把唯一能触发清理的信号藏起来——积压 80 条不是渲染问题，
是这个团队真的欠着 80 件事（铁律 #5）。而清理的机制 #259 已经建好了：`done` 由 agent 自己写，
`cancelled` **明确保留给用户**（`work_update_status` 的报错原文：「'cancelled' is the user's
decision」）。所以「agent 自行清理」与「请示 owner 清理」本来就是两条被区分开的路径。

真正缺的只是**没有任何东西会说「这块板子太大了」**——巡查只讲停滞条目，不讲尺寸。补这个信号是
[[patrol]] 的后续，单独 PR，阈值定在 30 条未完成（20 条 scrollback 是对话基线，常驻板子超过它
就说明积压压过了对话本身）。

**那个后续的措辞有一处必须小心**：提示只能是「盘点并向 owner 汇报」，不能是「把板子清短」。
`done` 是 agent 唯一能写的状态，一旦让它以尺寸为优化目标，它会把没交付的标成已交付——
板子变成谎报，而且看起来很像成功。#259 锁住 `cancelled` 挡住了一半，另一半正好敞在压力会落下的地方。

## 2026-08-11 (review 收口) — scrollback 与 cascade 都改用共享过滤

**scrollback 不再渲染平台自述行。** 此前它们会被写成 `"{sender}: {content}"`，读起来就是
「Alice: Team bulletin updated.」——因为通知的 sender 是触发它的人，而巡查的 `team_<id>` 标记
根本不过 `member_map`。agent 于是在回答**没有人说过的话**。

**`_team_cascade_depth` 补齐另外两种类型。** 它原来的注释把理由写得很清楚（平台在盘点、不是
agent 在发言；而且固定 LIMIT 窗口里被跳过的行仍占一个槽），那段话对停止通知和公告栏通知
**逐字成立**——只是写它的时候只有 patrol 存在。现在三种都走 [[system_messages]] 的同一个元组。

`TEAM_ROOM_OWNER_PREFIX` / `USER_SENDER_PREFIX` 的定义移到 [[team_schema]]（四个模块在构造或
匹配它们），本文件改为 re-export，既有 importer 不受影响。

## 2026-08-11 (review 收口 2) — 平台行**标注**，而不是丢弃

上一轮我把 `msg_type in PLATFORM_MSG_TYPES` 的行整条从 scrollback 里删掉——**这打断了
#259 的巡查追问链路**，而且断在最讽刺的地方：

巡查的回复是**带 @mention 发出去的**（prompt 明确要求 Leader「@mention the owner and ask
where it stands」），所以它会成为被点名成员这一轮的**触发消息**。而 prompt 里那句指认
（「You were just @mentioned by X. Respond to that message.」）**只印发送者、不印正文**——
依据正是它自己那句注释「it's already in the history above」。history 一旦跳过它，
这句指令就指向了 prompt 里不存在的东西：**被追问的 agent 对着空气回答，或者自己编一个问题。**

停止通知和公告栏通知都是 `mentions=None`，永远不会成为触发消息。所以**这条过滤唯一真正
想挡的那个 sender，正是它唯一弄坏的那个**。

现在渲染成 `[system] <content>`：内容留下，身份不冒充。指认行对平台 sender 也不再打印
`team_<id>` 这个合成标记（否则等于凭空发明一个队友，agent 还可能回 @ 它）。

**上一轮那条测试断言的是「内容不出现」——它把错误行为钉住了。** 现在断言的是真正要的性质：
内容在、但不被渲染成某个成员在说话。

## 2026-08-11 (review 收口 3) — 指认行的标签改为按类型分派，并补上断言

上一轮我给指认行加的「不冒充队友」分支**一条断言都没有**：把整段删掉回到
`who = _sender(tm)`，314 条测试照样全绿。同一段代码两轮内第二次在测试面上敞着——
上一轮是走 `trigger_messages` 的路径没人测，这一轮是走了却只断言了「内容回来了」。

现在断言分支两侧：`team_<id>` 不出现、且标签出现。标签本身改为 [[system_messages]] 的
`trigger_label` 按类型分派。

## 2026-08-11 — 与 #283 的已读游标并存

`rendered_from = history[0].created_at` 与公告栏加载并列，纯新增。

**但两者之间有一处语义耦合，值得写下来**：#283 的契约是「read = prompt 真的渲染过它」，
`rendered_from` 取的是「prompt 实际携带的最老一条」。这条前提**依赖平台行被渲染**。

上一轮我一度把平台行整条从 scrollback 里丢弃（后来因为打断巡查追问链路改回带标签渲染）。
**如果那个版本活到今天，这里就是一处真语义冲突**：被丢掉的行会随游标推进被标成已读，
而它们从未展示给任何人——正是 #283 那段 docstring 明确拒绝造成的静默丢失。

现在平台行渲染为 `[system] <content>`，前提成立。这也是"标注而不是丢弃"的第二个理由，
当时没人提出过。

## 2026-08-12 — team prompt 从「一串名字」变成一张卡

此前一个成员醒来时知道的全部是:自己的名字、"你在一个团队群聊里"、队友的**逗号分隔
名字串**、最近 20 条消息、共享目录路径。团队叫什么、为什么存在、队友是干什么的、
谁在忙 —— 一个都没有。

**团队卡**(`_team_card_lines`):名称 + `description` 全文 + `intro_md`(截到
`TEAM_INTRO_MAX_CHARS=1200`,在行边界回退以免切断 markdown 表格/代码块;截了就标注,
没截绝不标注 —— 对完整文本打截断标记是会连累其它标记可信度的小谎)。字段缺失渲染成
**什么都没有**而不是空标题。位置在房间头之后、共享目录之前:"我在哪、和谁、为什么"
是读下面一切的框,不能垫在机制说明底下。数据来自 `_team_board` 本来就查了的 `teams`
整行(它读完 `lead_agent_id` 就把其余丢了),**零新增查询**。

**roster**(`_team_roster` + `_roster_lines`):一行一个成员,格式对齐
`message_bus_module` 的 Known Agents(`` `id` — "name": "desc" ``,2026-09-11 起走共享编码器)。这不是审美 —— 那份
列表正是 agent 学到 `bus_send_to_agent` 要什么标识符的地方,两个面用两套标识符等于
逼模型去猜映射。**自己也在名单里并标 `(you)`**;lead 标记挂在**每一行**,于是非 lead
成员终于知道谁在负责(此前只有 lead 自己被告知)。描述未设置时整段不渲染,和 Known
Agents 同一条 2026-08-04 的教训。

数据层从「每个成员一次 `get_one` 只取名字」改成**三次批量读**。不做 JOIN:
`LocalMessageBus._db` 是 RAW backend,四表 LEFT JOIN 会是本包方言最脆的一句 SQL,而
一个 team 最多几十人,收益为零。有一条看门狗测试钉住"不许退回逐个查"。

**队友状态**(`_member_status`):只讲两件事 —— `running (3m)` 和
`running but no signal`。**idle 什么都不渲染**(常态挂在每个名字后面就是背景噪音,
和巡查"不要每几分钟报平安"同一条产品原则);**`phase` 不注入**(内部步骤名,给模型只
会诱导它评论队友的工具使用,而且每几秒抖一次)。时长取 `started_at` 而非 `updated_at`
(后者是心跳,永远约等于现在),整数,不给假精度。

活动行取自 `get_channel_activity`(**按 channel**)。这一点必须守住:
`bus_agent_activity` 主键是 `(agent_id, channel_id)`,只按 agent 取会拿到字典序靠前的
**别的房间**的行 —— 这个洞本仓库已经出过一次(巡查的 stalled 判定),别再来第二次。

**scrollback 的 @ 标注**:`BusMessage.mentions` 一直存在,这个 prompt 从没读过它。
现在逐行标 `[→ 谁]`(是自己就写 `you`)。知道一个请求已经有主,正是避免两个 agent
做同一件事的依据。

**多条 @ 逐条列**:此前只指 `trigger_messages[-1]`,更早那几条混在 scrollback 里像
别人的流量 —— 问了却被无声丢掉,在用户看来就是 agent 无视了它们。

**真假 @ 分支**:`routed_by == "default_responder"` 时不再说 "You were just
@mentioned",改成「X 发言时没有 @ 任何人,你是这个团队的默认应答人,所以它落到了你
这里;回答它,或按 roster 交给更合适的人」—— 顺带回答了"为什么是我"。

## 2026-08-12 (review 后) — 三处自我纠正

**① 缺口判定改用 `has_unread_before`。** 见 [[local_bus]] 同日条目:我用无 limit 的
`get_unread` 回答一个布尔问题,把同一批改动刚消灭的形状请了回来。

**② 真假 @ 的判据从「是不是只有一条」改成「整批是不是都被路由」。** 初版只在
`len(trigger_messages) == 1` 时区分 `routed_by`,于是用户在一个 poll 窗口(3-12s)里
连发两条都没 @ 人的消息时,复数分支照样打出「2 messages @mentioned you」——**要删掉的
那句谎话换了个分支活着**。现在按批次分组:全部路由 → 复数版的默认应答人措辞;全是
真 @ → 原措辞;混合 → 复数版并**逐条标注**哪条是路由补的(scrollback 的 `[→ …]`
已经证明逐条标注读起来没问题)。

**③ `activity` 覆盖参数删除。** 它在生产没有任何调用方,而 5 条状态测试全靠它驱动 ——
也就是说 `_team_roster` → `roster[i]["activity"]` → `_member_status` 这条**真实链路
一条测试都没走过**。参数删掉,测试改用真实形状,另补一条端到端用例:往
`bus_agent_activity` 写一行 running、外加**同一个 agent 在另一个房间的 idle 行**,
断言 prompt 里出现的是本房间的时长 —— 顺带把「必须按 channel 取」钉死。

顺带:roster 的描述与 capabilities 截断现在会标记(团队卡对 `intro_md` 就是这条规矩,
一份 prompt 里不该有两套标准);空 roster 不再说「just you」——这个 agent 自己就是
成员,读回空意味着读失败,不是房间空了。

## 2026-08-12 — 房间投递从"turn 之后"搬进"turn 之内"

`_post_to_room` 闭包取代了原先在 `_invoke_runtime` 返回后才执行的那段代发,并作为
`on_plain_text_delivery` 交给 runtime。**分层没有倒挂**:回调是数据往下流,而"投递
意味着什么"仍然全部留在这里 —— @mention 解析(工作交接靠它)、级联封顶(bus 策略)、
run id 盖章(transcript 靠它打开某一行背后的那一轮)。runtime 只决定这次投递**算不算
一次回复**。

事后那段必须删掉,不能留着:房间会把每句话说两遍,而"房间重复发言"比它要修的记账问题
更糟。有测试钉住"恰好贴一次"。

event_id 现在取 `watched_run_id[0]`(`on_event_id` 填的),因为回调跑在
`_invoke_runtime` 返回之前,那时还没有返回值可用。**这一点连带改了两个测试的桩**:
它们此前只 `return ("text", "evt")`,现在必须像 runtime 那样先报 run id、再把纯文本交给
deliverer —— 桩不模拟真实时序,测的就不是真实链路。

`send_message` 失败时回调返回 False,于是 step_3 不发帧:那一轮确实没回复,记忆里也就
不会出现一条房间从没收到的话。

## 2026-08-13 (review 后) — 三处自我纠正

**① 记账修对了,冷启动一分没修 —— 而我声称修好了。** 详见
[[chat_module]] 同日条目。此处只记教训:那一批测试全在孤立地验
`_delivered_to_origin` 和摘要文案,**没有一条走落盘**,于是"行类型跟着投递走"这个
断言从头到尾没被检验过。文件名承诺了一件事,四条测试一条也没验它。

**② team 房间的错误面被删掉了。** 投递搬进 turn 之后,`collection.is_error` 时
`_invoke_runtime` 返回的那条 ⚠️ 在 team lane 没有任何消费方 —— 而 turn 内那条路是
**刻意**在 loop 失败时不投递的(免得半截明文读起来像答案)。两头一夹,fatal 的 team
turn 变成"房间完全沉默"或"没标记的半截话"。

`_invoke_runtime` 因此在 `TurnResult` 上多带一个 `fatal`,team 分支在 `turn.fatal` 时**以房间身份**
(`from_agent=channel_owner`,即 `team_<id>` 标记)贴出通知。**刻意不走
`_post_to_room`**:那条路会解析 @mention(把队友拖进一次故障)、盖 run id、并被记成
agent 的一次回复 —— 在一个专门消除假账的改动里再造一笔假账。

沉默是这里最坏的结局:@ 了这个 agent 的队友分不清"不感兴趣"和"坏了",交接原地停死。
这正是 2026-04-20 那条通知存在的理由。

**③ 3.4.T 漏了取消门。** 取消是 Step 4 之后才抛(为了让被打断的 turn 也进历史),
所以 step_3 一定跑到底。缺这个门的代价不止是多一行:`_post_to_room` 会解析 @mention,
于是一轮**被用户中止的** turn 能把工作级联给房间里其他 agent,各自跑一整轮。判定抽成
`_should_deliver_team_reply` 纯函数 —— 和同文件 `_should_run_helper_llm_fallback`
同一个形状,否则这个门测不了(我第一版测试在测试里重写了一遍条件,生产代码删掉门它
照样绿)。

## 2026-08-13 (review 后) — 通知只在 fatal 时发,且失败要留痕

**判据统一。** 通知此前看 `collection.is_error`(任何错误帧),而 turn 内投递的门看
`captured_error`(只有抛异常)—— 两个不相交的判据。差集正好是两条真实路径:
recoverable 抖动 → 房间收到**正确答案 + 一条假的 ⚠️**;没抛出来的 fatal → 房间收到
**没标记的半截话 + ⚠️**,而 agent 自己历史里一个字都没有。现在两端都读 `severity`
(见 [[run_collector]]),`is_fatal` 才发通知。

顺带修掉一个既有行为:一次 provider 抖动会把整轮真实回复替换成 ⚠️。现在 recoverable
时返回真实输出,DM lane 的 inbox 也跟着受益。

**通知发送失败不再静默。** 原先用 `contextlib.suppress(Exception)` —— 而它保护的正是
房间**唯一**的故障可见面。失败无痕会让"房间安静了"退化成两个无法区分的原因:代码没跑
到,还是跑到了但发不出去。异常仍然吞(通知是 best-effort,不该拖挂 turn;而且这段在
`ack_processed` 之后,抛出去会触发 `record_failure` 把这条消息推向 poison 阈值 ——
把"通知没发出"升级成"这条消息永远投不出去"),但必须留一条 warning。

## 2026-08-14 — 重建时把自己的接线删掉了

按 dev 的 `TurnResult` 重建这条 lane 时,`_invoke_runtime` 的
`on_plain_text_delivery` 形参**和**它往 `run_and_collect` 的转发被一起删掉,而调用点
仍在无条件传它(DM 传 `None`,同样是这个关键字)。后果是**每一条 bus 消息 TypeError**
—— team 房间与 peer DM 全线停摆,消息几轮内到达 poison 阈值,owner 收到永久失败通知。

**全量 5945 条测试一条都没照出来**,因为这一片的测试**全都把 `_invoke_runtime` 整个
替换掉**,真实签名从未被执行。守门测试因此改用本仓已有的正确范式:桩
`run_and_collect`,让真实函数留在路径上(`test_bus_run_cancellation.py` 里那条转发
`cancellation` 的用例就是这么写的),并实测过删掉形参会红。

## 2026-08-14 (补) — `room_post_failed`:跨 turn 边界把一次异常递出来

代发搬进 turn 之后,失败的异常发生在**回调里**(turn 内),而处理它的地方
(`_announce_failed_room_post` —— 贴 `system_delivery_failed` 行、并把回复原文保进
owner 的 inbox)在 turn **之后**。两者之间只隔着 `run()` 的返回,但回调的返回值只能
是 bool,没地方带异常。

`room_post_failed` 是一个单元素 list,充当那条边界上的信箱:回调把异常放进去并返回
False(于是 step_3 不发帧 —— 那一轮确实没触达房间),turn 结束后 team 分支据此调
dev 的公告方法。

**为什么不在回调里直接公告**:那会在 turn 内往房间再写一条消息,而此刻 `run()` 尚未
返回、`ack_processed` 也还没跑,失败公告会先于"这一轮已处理"落库;更重要的是,公告
必须以**房间身份**发,而回调是以 **agent 身份**代发的那条路径,混在一起会让公告被记成
agent 自己的一次发言。

**为什么不用异常穿透**:回调是被 `step_3` await 的,抛出去会打断投递阶段并落进
runtime 的通用错误处理,把"一条消息没贴成"升级成"这一轮失败"。

## 2026-08-14 (再补) — 这个信箱要三态,两态会把"从没投递"记成一次成功的 hop

上一条描述的 `room_post_failed` 只区分"记到异常"与"没记到异常",于是**回调压根没被
调用**这第三种情形塌进了后者,被读成"投递成功了"。它不是理论情形:两道 fatal 门问的
不是同一个问题 ——

* runtime 侧(`step_3` 的 `_turn_hit_a_fatal`):`captured_error is not None` 或存在
  字面 fatal 帧 → 拒绝在 turn 内代发(半截流出来的文本会被读成一个答案);
* trigger 侧(`turn.fatal` ← `RunCollection.is_fatal`):看最后一条 error 帧的
  severity。

agent 已经先答过、随后 loop 抛异常的那一轮,runtime 侧拒发,而收尾帧是
`recovered_after_reply` —— 按 [[run_collector]] 的裁决语义**不算 fatal**。两边各自都对,
合起来的结果是:房间一个字都收不到,`turn.fatal` 为 False 所以不贴 ⚠️,`post_err` 为
None 所以不走失败公告,而 `posted` 停在初值 `True`,`[bus-timing]` 记下一次**什么都没
投递的成功 hop** —— 而这条序列正是用来判断"投递有没有问题"的那个指标。

现在这个信箱记 `("ok" | "failed" | "not_attempted", exc | None)`:

* `failed` / `not_attempted` 都把 `posted` 置 False —— 房间里没有这一轮,hop 就没完成;
* `not_attempted` 且非 fatal 时走同一个 `_announce_failed_room_post`(它的 `error` 形参
  因此放宽到 `Exception | str`):补救是一样的两件事 —— 房间留一行可见的投递失败、回复
  原文进 owner 的 inbox;是"发失败了"还是"压根没发"属于公告内容,不该是两条代码路径。

**不要从"没记到错"推断"投递成功了"** —— 这是本 lane 反复付过学费的同一类错误。

## 2026-08-14 (三补) — 公告之前先问房间;以及「平台没投递」不止一条臂

上一节把信箱改成三态,解决了「记账」;但 `not_attempted` 那条臂**公告**得太早。

让它可达的那条链自己带着答案:`turn.fatal` 为 False 唯一的来路是收尾帧为
`recovered_after_reply`,而这个 severity 的置位条件**就是** `_has_organic_reply` ——
agent 这一轮确实调过一次投递工具。三个工具打三个地方:

| 打给谁 | 房间听见了什么 | 该不该公告 |
|---|---|---|
| `bus_send_message` → 本房间 | agent 的话 | **不该** |
| `bus_send_to_agent` → 队友私聊 | 什么都没有 | 该 |
| `send_message_to_user_directly` → 只给 owner | 什么都没有 | 该 |

团队 prompt 禁用投递工具只是**文字规则**,MCP server 在团队 turn 里照常挂着(清空的是
expressive declaration,不是工具面),按铁律 #15 平台不管模型听不听话 —— 所以第一行不是
边角情形,而是不听话的 agent 在团队房里「发一句话」最顺手的写法。

于是这条臂改成先问 `has_message_from_turn(channel_id, agent_id, turn.event_id)`,听见了
就只记账不公告。**不能用 `turn.delivered` 当门**:它认的名单含
`send_message_to_user_directly`,用它会把上表第三行那条**正确**的公告一起吞掉,房间又回
到静默。为让这个问题可回答,MCP 侧的 `bus_send_message` 同批补盖了 `event_id`(见
[[_message_bus_mcp_tools]])。

记账与公告是两个问题:即使房间听见了 agent 自己那句,**平台代发**这件事确实没发生,
`posted` 保持 False 是对的。

同批补上 `reached_nobody` 那条臂的 `posted = False` —— 它的定义就是「没文本、也没有任何
工具触达任何人」,房间只收到一行平台通知,而**通知不是投递**。上一节在有文本那一支立的
判据,这一支漏了。

`_post_to_room` 的 mentions 解析与 cascade 深度读取也一并纳入 `try`:它们在 try 之外时
抛出去会被 step_3 接住返回 False,而信箱停在 `not_attempted` —— 于是 `_team_cascade_depth`
的一次 DB 故障会被写成「runtime 拒绝投递」,把排查引到另一道门上。**调用过就失败,一律记
`failed`**。

## 2026-08-14 (合 dev #303) — turn 内代发必须走 `_post_to_room`,而不是 `_bus.send_message`

#303 把「发帖」与「叫醒 poll loop」绑成同一个方法,理由是它们分开过一次:巡查那条路径
漏了 wake,被 @ 的队友因此白等一个完整的自适应轮询间隔。本 lane 的 turn 内代发是**同一
类**调用点,而且是更主要的那条 —— 团队回复的下一跳就是队友。合并时闭包直接调
`self._bus.send_message` 会静默绕开 wake,把刚修掉的死气原样引回主路径。

两件事同批:闭包改名 `_deliver_reply`(dev 的方法就叫 `_post_to_room`,重名会让「唯一入口」
这条规矩看起来有两个入口),fatal 通知那条 post 也改走同一入口。dev 自带的两条 wake 测试
原来的桩在 turn **外**代发,对齐生产后它们才真的覆盖这条新路径 —— 实测过绕开入口会红。

### 占用画像同时被改了,而这一点原来没写下来

搬进 turn 内改的不只是记账时点,还有**槽位占用的重叠关系**。旧顺序:A 的 turn 跑完 →
代发 → B 成为候选 → A 释放槽位 → B 拿到槽位,重叠约等于零。新顺序:A 第 5 秒就把回复
投进房间 → `_wake()` 立刻叫醒 poll loop → B 立刻被派发,而 **A 的 turn 还在跑** ——
按铁律 #14,回复之后继续干几十分钟是一等场景,不是异常。于是同一个房间一次 D 跳接力,
峰值占用从 1 个槽位变成最多 D 个,每个都可能长时间不释放。

这不是 wake 引入的:没有 wake,B 也会在下一个轮询周期(3-12s)被派发,重叠一样存在,
wake 只是把「最多晚 12 秒」变成「立刻」。但本轮是这两半第一次相遇,也是第一次有了
`bus_max_workers` 这个旋钮和 `worker_starvation` 告警,所以在这里记下。

worker 池是**进程级、跨用户共享**的,所以后果不局限在这个房间:几个房间同时接力就能
把 8 个槽位占满,其余用户的团队房与 peer DM 全部堵在信号量后面。`settings.py` 里
`bus_max_workers` 的注释已同批补上这条,重点是**别把 `worker_starvation` 直接读成
「有人的 agent 卡住了」**。

**能动的只有平台侧的槽位数量与分配策略。** 给 `agent_loop` 加时间/轮数上限、或「回复
后强制结束 turn」是铁律 #14/#15 明令禁止的方向,也正是本 PR 通篇在维护的前提。按 channel
轮转的公平派发是正解,但它是独立的一条,已记进 followup。

## 2026-08-12 — 把 segments 交给 bus

`_invoke_runtime` 返回值追加第三个元素 `segments`。

**第一版我写的是 `getattr(collection, "segments", None)`——而那个作用域里根本没有 `collection`。**
`getattr` 的默认值会把这个错误吞成一个永久的 `None`：功能死掉、不报错、不记日志、测试全绿。
改成真正的返回值后，契约变化立刻炸出 11 个既有测试——那才是它该有的响亮失败。

## 2026-08-12 — 级联封顶落墙

此前只有 `logger.info`。通知发在**回复之后**，让房间按事情发生的顺序读：
agent 先说话，然后平台解释它没做什么。名字用 `member_map` 解析成显示名——
「agent_b 没被拉进来」不是用户能行动的句子。

## 2026-08-18 — 删除 `_record_errands`（合并残留的死代码）

与 dev 的 PR #310 合并后，trigger 侧这份差事记账已无调用者：它原本由 `_deliver_reply` 调用，
而该方法随「房间改为工具调用」一并移除。活的实现在 [[team_posting.py]]，理由同级联上限——
这是「把消息放进团队房间」的属性，不是「恰好触发了本轮的循环」的属性。

## 2026-08-18 — 删掉级联上限的三个重复符号与死常量

`MAX_TEAM_AGENT_HOPS`、`_extract_team_mentions`、`_team_cascade_depth` 在
[[team_posting.py]] 落地后是**复制**而非移动，trigger 侧仍留着一份；`_team_cascade_depth`
已无生产调用者，而「平台行必须在 SQL 的 WHERE 里排除、不能取回后再过滤」这条上限真正依赖
的不变量，测试只钉在这份死代码上 —— 从活的查询里删掉排除子句，整个测试套照样全绿。

删除前先把断言搬到活实现上，并发现搬过去的断言本身也钉不住：`depth >= MAX_TEAM_AGENT_HOPS`
在两种读法下都成立（平台行自己就把计数凑满了）。改成精确值构造 —— 一条用户发言 + 三个真实
hop + 四条平台通知，窗口只有 6 行：排除在 SQL 里得 3（正确），取回后再过滤得 6，且用户那条
「重置点」被挤出窗口从此不可见，于是刚被用户重启的三跳链会被误判超限、@ 被剥掉。两条测试
均做过变异验证。

顺带删除三态 `POST_OK`/`POST_FAILED`/`POST_NOT_ATTEMPTED`（铁律 #2）。

## 2026-08-21 — 并发粒度 agent → LANE (agent, channel)(live-steering 前置)

痛点:一个 agent 服务多个 team 时被串行 block(在 team A 跑,team B 消息只能等)。改法:并发单元从
**agent** 变 **lane=(agent_id, channel_id)**。`_in_flight`/`_agent_locks` 按 lane keyed;`_agents_with_pending`
→`_lanes_with_pending`(SQL 多返 channel_id);`_dispatch(agent,channel)`/`_process_agent`→`_process_lane`
(单 channel);`_poll_cycle` 按 lane 判在飞 + dispatch;patrol lane=(lead, 房间);heartbeat/stop 迭代 lane。

**事故防护全保留**:per-lane 锁**照样防"同一条消息 dispatch 两次"**(重复风险本就是每 channel 每消息级,
2026-05-12 msg×3 扣 30K token 事故);每 dispatch 独立 supervised task + semaphore + liveness(2026-07-27
33h stall)不变。circuit-breaker 门在 `_process_lane` 里、碰 bus 前(paused agent 不读 bus)。

`_process_agent(agent)` 保留为**跨 lane 集约**(委托 `_process_lane` per channel + 同一 circuit-breaker 门),
不含独立逻辑——供按 agent 思考的调用方/测试;生产走 per-lane 并发 dispatch。

## 2026-08-21(补)— steer 路由:in-flight lane 的新消息注入活 run

`_poll_cycle` 里 lane 在飞时不再干等,调 `_route_steer`。**起可 steer 的 run**:team turn 在
`_handle_channel_batch` 建 `SteerChannel`,`on_event_id`(run id 绑定处,同 cancel watcher)登
`RunRegistry(agent, channel, run_id, channel)`,`stack.callback` 无论怎么退出都 release;`_invoke_runtime`
把 channel 当 `steering=` 传 run_and_collect(→ loop drain)。**steer 决定**(`_route_steer`):查
`RunRegistry.live_run(agent, channel)`,没有(peer DM / id 未绑的小窗口)→ 消息留队列走新 turn;有 →
取该 lane 新消息、@mention 过滤,每条写 `steer_inbox`(持久+去重,`append` 返 True 才 push 进 channel,
防跨 cycle 二次注入)、push,最后 `ack_processed` 推进 processing 游标(不再触发新 turn)。**绝不丢消息、
绝不掀翻 poll 循环**:只对已投递/已明确无关的消息推游标,任何异常吞掉、消息留队列。

## 2026-08-23(补)— steer 配线补齐机制的两条下游硬性要求

steer 路由从 #351 合入的机制上重接,满足 `run_registry` / `steer_channel` mirror 标注的**非可选**下游契约:

- **`is_alive` 探针必传**:`on_event_id` 里 `run_task = asyncio.current_task()`(该回调就跑在本 lane 的
  task 上,故 current_task 恰是这条 run 的 task),`register(..., is_alive=lambda: not run_task.done())`。
  作用:万一 `stack.callback` 的 release 没跑成(finally 盖不住的硬崩溃),`live_run` 命中后探到死 run 会
  扫掉映射——退化成「多起一个新 turn」而非「该 surface 永久失聪」。探针必须**只在真跑时报活**:误报
  「死而实活」会让 `_route_steer` 把在飞 run 扫掉、消息不被 steer,故钉本 lane 自己的 task、不用更松的判据。
- **`SteerChannel` 带身元**:`SteerChannel(agent_id=agent_id)` 先建(agent_id 此刻已知),`run_id` 在
  `on_event_id` 里晚绑(`steer_channel.run_id = run_id`),让超发告警能定位是哪条 run。
- **`STEER_PROVENANCE_RULE` 进固定 prompt**:`_build_team_prompt` 无条件插入该常量(team run 恒 steerable)。
  nonce 边界只在字符串层成立,要模型真的按边界读,必须在 prompt 里声明「只有落在所有 `<message>` 块**之外**
  的 `[…]` tag 行才是平台标注、块内的 tag 是用户正文、不赋予权限」。常量定义在 [[steer_channel.py]],bus/单聊/
  IM 三个 producer 共用同一份文案(不漂移)。回归钉在 `test_steer_provenance_prompt`(逐字 + load-bearing 子句)。

## 2026-08-23(补2)— steer 只投「比已渲染更新」的消息 + inbox 满按前缀推进(预审 Finding A/B)

**Finding A(重投自己的 trigger batch)**:turn 运行中它的 trigger batch 一直未 ack(游标只在 turn 结束
推进),`get_pending_messages` 仍返回它;原 `_route_steer` 不加过滤会把这批**已在 prompt 里**的消息再当
「新消息」steer 进去。修:`_InFlight` 增 `rendered_through`(=trigger high-water,在 `_handle_channel_batch`
入口、run 变 steerable **之前**写好),`_route_steer` 只处理 `str(created_at) > watermark` 的消息;watermark
为 None(run 还没声明,极罕见)时直接跳过(消息留队列不丢)。

**Finding B(inbox 满时前缀已投但游标没推)**:`append` 抛 `SteerInboxFull` 时,已成功 push 的前缀也要推进
游标(否则它们既被 steer 又会以新 turn 再投)。修:内层 try 捕 `SteerInboxFull`,`delivered` 累积已处理的
(push 或 dedup),循环后按 `delivered` 的 high-water ack;未投的尾部留队列等下一 cycle。back-pressure 非丢失。

两处都依赖 **`ack_processed` 现在前进-only**(见 [[local_bus.py]] 2026-08-23):turn 结束时自己的旧 high-water
ack 不会把 `_route_steer` 推进过的游标拉回。回归:`test_steer_routing` 的
`test_route_steer_does_not_reinject_the_turns_own_trigger_batch` /
`test_route_steer_advances_cursor_for_the_delivered_prefix_on_inbox_full`(均已变异验证)。

## 2026-08-23(补3)— 删 `_process_agent` 兼容垫片 + 两处 get_pending 走 channel scoping（review #4/#6）

**#6**：`_process_agent`（跨 channel 集约器）生产零调用方（生产走 `_run_dispatch → _process_lane`），是铁律 #2
禁止的兼容垫片（为不动测试保留、自带重复熔断门 + 多余一次 get_pending）。删除；~40 处测试调用改直接叫
生产的 `_process_lane(agent, channel)`（各 e2e 都是单房间常量：`CHANNEL`/`ROOM`/`ch_dm`）——现在 e2e
（cascade / relay wake / unread cursor / delivery / 熔断门）真跑在生产 per-lane 路径上，不再是绕开的形状。
**#4**：`_process_lane`（`:994`）与 `_route_steer`（`:1124`）删掉 Python `m.channel_id == channel_id` 过滤，
改传 `get_pending_messages(agent, channel_id=channel_id)`，LIMIT 落单房间（见 [[local_bus.py]]）。
熔断门在 `_process_lane` 里、bus 读之前、semaphore 之前，语义与旧 `_process_agent` 一致（`test_bus_circuit_breaker_gate`
现直接叫 `_process_lane` 验证生产门）。

## 2026-08-23(补4)— 诊断不倒退 + 改名（review #7 + Minor）

**#7**：并发切成 lane 后一个 agent 可同时占多个 slot（每个活跃房间一个），但 `liveness_snapshot` 原来把最长
运行 lane 折叠回 agent、丢了 channel——2026-07-27 33h stall 的定位靠的正是「哪个房间卡住」。改：新增
`longest_running_lane`（`agent@channel`）+ `distinct_agents`（区分「一个 agent 霸池」vs「多 agent 各占一个」），
`_check_worker_starvation` 的 warning 与 audit detail 都报 lane + distinct_agents；`longest_running_agent` 保留给旧读者。
回归钉在 `test_bus_worker_starvation`（audit detail 断言 `longest_running_lane`/`distinct_agents`）。
**容量**：不加硬性 per-agent 上限（与铁律#14 精神冲突、也是本功能目的）；**合 dev 前需 ops 复核 `settings.bus_max_workers`
当前取值**——fan-out 从 agent 数变 agent×活跃房间数。
**Minor**：`_agent_locks`→`_lane_locks`（存的就是 lane key）；删「Per-agent serialisation」过时首行；类 docstring
「Finds agents」→「Finds LANES」。

## 2026-08-23(补5)— 消费契约:游标随「真消费」前进(review Critical #1/#2/#3 + #5)

**根因**：原 `_route_steer` 在 `push`(=put_nowait 到 asyncio.Queue,永不失败、不代表被读)后就推 bus 游标。
loop 的 DRAIN_STEERING 在每 step 末尾、STOP_CHECK 之前——一旦决定收尾就再无第二次 drain,游标却已推过。
三窗口(末次 drain 后 / turn 失败 / release 竞态)都能让「@了 agent、它从没看见、也不重投」→ 静默丢失。

**正解(跨 5 层,team turn 走 subprocess、runner 刻意 DB-free,故消费证据经 stdout 逆流)**:
1. producer(`SteerChannel.push`)给 provider message 盖私有键 `_steer_id=msg_id`([[model.py]] `STEER_ID_KEY`);
   `remember(msg_id, canonical_ts(created_at))` 记游标水位。
2. consumer(`QueueSteeringInlet.drain`,见 [[steering.py]])**剥** `_steer_id`(模型看不到)、累积到 `take_consumed()`。
3. loop drain 后 `yield` 一个 **`TYPE_STEER_CONSUMED`** 事件(transient ui-track,见 [[loop.py]]/[[events.py]])。
4. runner `serve_turn` 把它写成**独立行** `{"steer_consumed": ids}`(绕过 legacy adapter 白名单,见 [[runner.py]]);
   driver `_run_subprocess`/`_run_inprocess` **拦截**该行 → `steer_channel.deliver_consumed(ids)`(见 [[nexus_agent.py]]),
   **不**转发给 AgentRuntime。
5. `SteerChannel.deliver_consumed` 用 `remember` 的映射取最新 created_at,回调 `on_consumed(ids, latest)`。
6. bus 的 `on_consumed`=lambda→`_ack_steer_consumed(agent, channel, run_id, ids, latest)`:`mark_consumed_by_msg_ids`
   (retention+背压,见 [[steer_inbox_repository.py]]) + `ack_processed` + `ack_read` 到 latest。

**`_route_steer` 现在 delivery-only**:append+push+remember,**从不碰游标**。故 push-但-没-drain 的消息留 pending→
新 turn 重投(不丢);已消费的游标已过(不双投);un-addressed 也不 ack(避免跳过更旧未消费 steered 消息)。

**无 poll/turn race**:`on_consumed` 由 driver 在**turn 任务**里(迭代 turn 事件时)触发,与 turn 末尾 ack 同任务串行;
且 `ack_processed`/`ack_read` 都是前进-only(补1/补3)。所以 read 游标也在这里推(reviewer #3 的 `steered_through`
方案被这个更根因的消费驱动取代——两个游标同源、同任务)。

**#5 消解**:`_route_steer` 不再有「已投递前缀推游标」,SteerInboxFull/任何异常都只是「投一半、其余留 pending」,
无游标可漏→无双投。内层 `except SteerInboxFull` 仅做优雅停+日志。

**Minor**:steer 水位/比较过 `canonical_ts`(space 格式串会排在所有 'T' created_at 之下,坑);`rendered_through`
在 `get_recent_messages` **之后**抬到 scrollback 最新(否则 DB 往返间到达的消息既进 scrollback 又被 steer=一 turn 看两遍)。
回归:`test_steer_routing`(delivery-only / 消费推双游标 / inbox 满留 pending / 不重投 / 前进-only)、
`test_nexus_inprocess_steering.test_consumed_signal_flows_back...`(5 层 e2e)。

## 2026-08-23(补6)— 剩余 Minor 决议

- **qsize 节流(steering.py docstring 点名交给 steer-routing PR)对 bus producer 记为 N/A**:每轮 push 量由**到达率**
  (poll 周期内新消息)决定,不是「一次把 backlog 灌进去」;真正的背压边界是 `steer_inbox` 的 `SteerInboxFull`(未消费 500 上限),
  且现在游标随消费前进,in-flight 队列随 loop 每 step drain 收敛。故不读 `qsize()` 节流;`qsize()` 保留供未来上层用。
- **steer 投递的 `[bus-timing]` hop 计时**:暂缺(steer 消息不产生 queue_wait/hop 记录),记为**已知观测缺口、后续补**——
  与本 PR 的正确性正交,不阻塞;补的时候给消费 ack 一条同族计时行(延迟收益=「插话不等 turn 结束」的度量)。
- **`_route_steer`/`_ack_steer_consumed` 每次 `await get_db_client()`**:客户端是进程/loop 单例,可忽略;不额外缓存。

## 2026-08-24(补7)— 增量 review 修复:read 游标单写入方 + 未寻址带 floor ack + 孤儿行回收

**修正 补5 的过时描述**:read 游标**不再**在 `_ack_steer_consumed` 里推(补5 说「read 游标也在这里推」已作废)。

**🔴1(read 游标旁路 gap 保护)**:`_ack_steer_consumed` 只留 `mark_consumed_by_msg_ids` + `ack_processed`,
read 游标降级成在 `_InFlight.steered_through` 记水位;turn 末尾 `_ack_room_seen`(唯一 read 写入方、持有
`has_unread_before` gap 闸门)用 `max(canonical_ts(trigger), steered_through)` 作上界。**子情形2**:一次 steer
里有新消息没被 steer(未寻址/prompt 构建后到达)→ `_route_steer` 在 flight 打 `unsteered_gap`,`_ack_room_seen`
见到就退回只用 trigger(那条从没渲染的消息保持未读)。**read 游标恒等于:一个写入方 + 一道闸门。**

**🟡1(未寻址不 ack → 繁忙房间 >50 杂音后 steer 停摆)**:`_route_steer` 恢复对未寻址批次推 `ack_processed`,但带
**floor**=本 run 最老未消费 steer 的 created_at(`SteerChannel.oldest_unconsumed_created_at()`,每次重取);只 ack
严格早于 floor 的未寻址消息(绝不跳过未消费 steered),**绝不 ack_read**(未寻址从没渲染=🔴1 红线)。回归:
`test_a_busy_room_does_not_starve_live_steering_past_the_limit_window`(50 杂音+1 @mention,断言第二 cycle 能 steer)。

**🟡2(push 但从没 drain 的孤儿行永不可回收)**:run release 时 `stack.push_async_callback(_discard_steer_orphans)`→
`SteerInboxRepository.discard_run(run_id)`(`DELETE … consumed_at IS NULL`,不是 mark_consumed——标假 consumed 会污染
「哪些真进了模型」的审计)。**必须在最后一次 drain 之后**(AsyncExitStack unwind,所有 deliver_consumed 已跑完),
放 release 之前会与 in-flight deliver_consumed 竞态。回归:`test_discard_run_*`(SQLite+MySQL twin)。

## 2026-08-24(补8)— 孤儿回收顺序修正 + release 竞态 re-check(增量 review 🟡1/🟡2)

**顺序修正**:`_discard_steer_orphans` 必须跑在 `release` **之后**(release 后 run 出 RunRegistry,没有 `_route_steer`
能再找到它 append)。`AsyncExitStack` 是 **LIFO**,所以把 discard 的 `push_async_callback` 注册在 release 的
`stack.callback` **之前**(先注册=后退栈=后跑)。补7 描述的「release 时/之后」现在才真正成立。

**release 竞态 re-check**:光调顺序还剩「句柄在 release 前拿到、append 在 discard 后落地」的窗口(poll 任务的
`_route_steer` 在 `live_run()` 之后要 await 几次 DB)。故在 `inbox.append` 之前复查
`get_run_registry().live_run(agent,channel) is run`——**句柄身份**,不是 `is_alive`(lane task 在 release 后仍 alive,
探针会误报活着,round1 已点过)。不匹配→整批跳过且**不 ack**(消息留 pending 走新 turn,不升级成丢失)。

**测试**(增量 🟡2,都变异验证):`test_floored_unaddressed_ack_never_jumps_past_an_unconsumed_steer`(steer 但不消费+
更新的未寻址杂音,断言在**游标**上——floor 那条唯一会丢消息的分支;删 floor/`<`→`<=` 变红);
`test_route_steer_skips_the_batch_if_the_run_released_mid_flight`(在 `_get_channel_info` await 里 release,断言不 append、
消息留 pending)。`_discard_steer_orphans` 参数改名 `run_id_cell`(late-bind holder 自解释)。

## 2026-08-24(补9)— re-check 是「收窄」非「关闭」+ 结构性兜底(Opus 预审 C)

诚实标注:`live_run is run` re-check **收窄**但不**关闭**竞态——其后 `get_db_client` + `inbox.append` 仍 yield,
release 落在这最后窗口仍可能留一行未 drain。**不丢消息**(游标没动→重投);那罕见残留行由
`cleanup_older_than_days` 的**未消费孤儿臂**(`orphan_days=7`,见 [[steer_inbox_repository.py]] 补3)回收=结构性兜底。
loop.py:1244 有对应注释。

## 2026-08-24(补10)— read 游标闸门收口 + retention tick 接线(第4轮 review 🟡1/🟡2)

**🟡1(re-check 抹掉 gap 闸门输入)**:上一版 `live_run is run` re-check 的 `return` 在 `unsteered_gap=True` 之上,
早退时保守标记没举起 → `_ack_room_seen` 可能外推 read 游标越过从没渲染的消息。修:①`unsteered_gap` 提旗到 re-check
**之前**(纯内存保守标记,不是 DB 写/ack,必须活过早退);②`_InFlight.steer_cycles_in_flight`(`_route_steer` 入口 +1、
`finally` −1),`_ack_room_seen` 把 `>0` 与 `unsteered_gap` 同等对待——「无法证明没有 gap 就不外推」。read 游标闸门=
「单写入方 + 双输入(旗/在途计数),默认保守」。回归:`test_release_race_still_raises_the_unsteered_gap_flag`(早退仍举旗)、
`test_ack_room_seen_holds_read_when_a_steer_cycle_is_in_flight`(在途 hold)、floor 等值边界 —— 三条都变异验证。

**🟡2(cleanup 全仓零调用方 → steer_inbox 只写不删)**:MessageBusTrigger 是 steer_inbox **唯一生产写入方**,故它接
retention tick:`_maybe_run_steer_cleanup`(poll loop 里 heartbeat 后调,`_last_steer_cleanup_monotonic` 门控 startup+每日),
调 `cleanup_older_than_days(STEER_RETENTION_DAYS=3, STEER_ORPHAN_DAYS=7)`。策略常量在**调用方**(trigger 模块级),repository
只做 CRUD。加 `idx_steer_inbox_created`(两臂都过滤 created_at,否则 MySQL 全表扫)。**补9 的「structural backstop」现在
真接上了**。回归 `test_steer_cleanup_tick_actually_calls_the_repository_gated_daily`(测**接线**——tick 真调 repo、且日级门控,
不是只测 repo 方法,避开上轮「有没有人调」那个坑)。
