---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/job_trigger.py
last_verified: 2026-09-10
---

## 2026-09-10（review r3 M1）— 同一次调用内按执行主体 memo

`_poll_and_enqueue` 的入队门对每个 `exec_uid` 只调一次 `_non_transacting_status`（局部 dict `standing`）；
`_resume_eligible_no_quota_jobs` 对每个 `exec_uid` 只调一次 `_user_can_run`；`_resume_spend_capped_jobs`
对每个 `(exec_uid, tz)` 只扫一次 `_daily_spend_cap_exceeded`。memo 只活在单次调用里，不跨轮，判定结果与
逐 job 查询相同（同一次调用内同一主体本来就得同一答案）。
锁：`test_poll_and_enqueue_looks_each_principal_up_once`、`test_backstop_checks_each_principal_once_per_call`、
`test_backstop_scans_each_principal_and_timezone_once`。

## 2026-09-10（review r1 C1）— `PAUSED_SPEND_CAP` 每日自愈：`_resume_spend_capped_jobs`

首版把花费封顶的 job 标成 `PAUSED_SPEND_CAP` 之后**没有任何路径能把它拉回来**：不在
`_RESUMABLE_STATUSES`，不被任何 backstop 扫描，`get_due_jobs()` 也永远捞不到它——一个
「日上限」变成了永久停服。现在 `_poll_and_enqueue` 的 900s backstop 段（紧跟
`_resume_eligible_no_quota_jobs`）多跑一个 `_resume_spend_capped_jobs()`：扫
`PAUSED_SPEND_CAP`，对每条 job 用**同一个** `_daily_spend_cap_exceeded(exec_uid, job时区)`
重判（先算花费再翻状态；I6 的「今天」口径已定，暂停与恢复用同一个「今天」），未超就
`compute_next_run(last_run_utc=now)` 向前推 + 翻 ACTIVE + 清 `paused_reason/paused_at`。
于是 job 恰好在「一次全新启动本来就会被放行」的时刻恢复：用户当地次日零点之后，或 ops
调低/关掉 cap 之后（cap=0 → 谓词恒 False → 全部恢复）。

**为什么不并进 `_resume_eligible_no_quota_jobs` / `rearm_user_no_quota_jobs`**：那两条按
provider readiness（登录/存 provider 的 edge）恢复，与花费无关，会在仍然超标时把 job 拉活，
下一次 poll 又原地暂停——正是 `_EDGE_ONLY_RESUME_REASONS` 注释在防的重试风暴。

恢复向前推而不是补跑：一条停了一天的 2 小时心跳 job 恢复瞬间不能补 12 次。next fire 落在
`end_at` 之外的周期 job 直接 COMPLETED（与 `_rearm_cooled_jobs` / `_heal_unscheduled_active_jobs`
同一条规则；注意 `_resume_eligible_no_quota_jobs` **没有** horizon 判定，是既有缺口，不在此列）。手动恢复也通了：[[job_recovery]] 的 `_RESUMABLE_STATUSES` 与前端 `canResume`
同批加入该状态。spend_cap 的 inbox 文案改成说实话（次日自动恢复）。

锁：`test_backstop_resumes_a_spend_capped_job_once_under_the_cap`（含 next_run 必须在未来）、
`test_backstop_keeps_the_job_paused_while_still_over_the_cap`、
`test_backstop_resumes_when_ops_disable_the_cap`、`test_poll_cycle_runs_the_spend_cap_backstop`（接线）；
r2 M-d 补：`test_backstop_completes_a_job_whose_next_fire_is_past_end_at`（COMPLETED + 清 next_run + 实例 completed）、
`test_backstop_judges_each_jobs_own_timezone`（同一笔花费，Asia/Shanghai 的 job 恢复、UTC 的 job 仍封顶）。

## 2026-09-10（review r2 I2）— 超长报告分片投递，超预算则明说，绝不静默丢

`_deliver_to_origin`：bus 写入边拒绝超 `MAX_BUS_MESSAGE_BYTES` 的行后，这里原本只剩
`logger.error`——「房间一定能听到回报」的保证被静默打破。现在 `multipart.split_for_bus` 按字节
切成 `part_index/part_count` 序列走同一条 `send_message`（收件 lane 会重组成一条）；超过
`MAX_MULTIPART_TOTAL_BYTES` 则贴一条平台说明「报告 N 字节超限、未贴出、留在 run 记录里」。
并发写入核对：写入边只沿**同 sender 的最近一块**链接（`part_group IS NOT NULL`），agent 自己的
普通 `message_team` 行不会打断；各块在同一协程里背靠背发出，链条连续。**未覆盖**（r3 M5）：
同一 agent 两份 ≥60 KB 报告同一瞬间投同一房间会交错链条，第二份的 part 2 被写入边拒、落成一行
`logger.error`——概率极低，先不加锁，注释已写明。分片进 team room 的两个代价（r3 M4）：transcript
把报告渲染成 N 条原始 part 行；origin channel 的 owner 若是 agent，其 lane 在本协程中途死掉时最多
被扣 grace。超预算那条说明行**以 agent 身份**发（r3 M7）：要带 run 的 event_id/root_run_id，
「查看推理」与级联停止才追得到产出它的 run。锁：
`test_job_origin_and_identity.py` 末尾两条（70 KB → 两块且拼回原文；>200 KB → 一条说明）。

## 2026-09-10（review r1 I6/M6/M7）— 花费上限改成「用户当日全部花费」+ 以 job 时区算「今天」

复审指出三处口径漂移，全部落在 `_daily_spend_usd_for_user` / `_daily_spend_cap_exceeded`：

- **统计的是用户全部 LLM 花费，不止 job**。`cost_records` 没有 job 归因列，交互聊天、
  memory consolidation、helper 调用、别的 job 全部计入同一个用户的当日总额。这是这个闸的
  真实语义（「这个用户今天花够了」是用户属性，不是某条 job 的属性），所以 env 从
  `NARRANEXUS_JOB_DAILY_SPEND_CAP_USD` 改名 **`NARRANEXUS_USER_DAILY_SPEND_CAP_USD`**，
  `.env.example` 明写「统计该用户当日全部花费」。不做 `call_type` 过滤：job run 与
  interactive run 写的 `call_type` 相同，过滤不出「只算 job」。
- **「今天」= job 冻结时区（`trigger_config.timezone`）的当地零点转 UTC**，与本文件其他
  时间判定同一口径（`_execute_job` 里就是同一个 `user_tz`）。此前用 UTC 零点：UTC+8 用户
  的额度在当地上午 8 点重置，一个「日上限」横跨两个自然日。`local_day_start_utc(tz, now)`
  是这条口径的唯一实现（未知时区回退 UTC 并告警，不让整个闸失效）；`now` 参数只给测试钉边界。
- **SQL 只做预过滤，精确边界在 Python 判**。SQLite 里 `created_at` 文本有两种形状：列默认
  `datetime('now')` 写空格形 `YYYY-MM-DD HH:MM:SS`，显式写入落 ISO `T` 形——同一天内 `T` 排在
  空格之后，任何单一字符串 cutoff 在边界日都不可能精确。于是 SQL 用「零点前一整天」的
  `YYYY-MM-DD HH:MM:SS` 字符串预过滤（只会多捞、绝不漏），再用 `coerce_utc`（两种形状 +
  MySQL 原生 DATETIME 都能读）逐行按瞬时比较。改传字符串也顺手关掉了 M6 指出的
  CPython 3.12 起 deprecated 的默认 datetime adapter 依赖。
- **前提：DB 会话时钟 = UTC**。`created_at` 由列默认填（SQLite `datetime('now')` 恒 UTC；
  MySQL `CURRENT_TIMESTAMP(6)` 取 SESSION 时区）。MySQL twin
  `test_db_session_clock_is_utc` 断言 `NOW() = UTC_TIMESTAMP()`，会话不是 UTC 就在测试里红，
  不在生产里静默偏移。
- M7：`import os` 挪到模块顶部。

锁：`test_local_day_start_follows_the_given_timezone`（三个时区三个零点）、
`test_sums_from_the_local_midnight_of_the_jobs_timezone`（同一天里 ISO 形 + 空格形各一前一后，
上海口径 5.0 / UTC 口径 13.0）、`test_execute_job_judges_today_in_the_jobs_timezone`（15:30Z 的
花费在 UTC 口径下已触顶、在上海口径下未触顶 → job 照跑）+ MySQL 同款三条。

## 2026-09-09 — B-14：每次调度前查日花费上限

`_execute_job` 拿到执行锁之后、build prompt/调框架**之前**新增一步检查：
`_daily_spend_cap_exceeded(exec_uid, user_tz)` 读 `NARRANEXUS_USER_DAILY_SPEND_CAP_USD`
（0/未设=禁用，读的是环境变量，不是 pydantic Settings——跟
`NARRANEXUS_ONBOARDING_GUIDE_AGENT` 等既有 `NARRANEXUS_*` 开关同一套
`os.getenv` 读法）与 `_daily_spend_usd_for_user`（新增裸 SQL，
`cost_records` 没有 repository；口径见上一节）比较，超了就把 job 标成
`JobStatus.PAUSED_SPEND_CAP` + `paused_reason="spend_cap"`，复用 B-17 的
`_notify_owner_job_paused` 通知 owner，然后 **return，从不 build prompt
也不调 `_run_agent`**——只挡下一次调度，跟既有 `PAUSED_NO_QUOTA` 同形，
绝不打断正在跑的 run（铁律 #14）。

（首版还把 `trigger_config.max_tokens_per_run` 透传进 `trigger_extra_data`；
复审 I7 查明没有任何消费方，已连同 schema 字段一起删除，见 [[job_schema]]。）

**Fail open**：`_daily_spend_cap_exceeded` 对 env 值解析失败或 DB 查询失败
都返回 `False`（不暂停）——一次数据库抖动不该冻结全平台的 job。

## 2026-09-10（review r1 I1）— `_is_user_banned` → `_non_transacting_status`：认整套 `NON_TRANSACTING_USER_STATUSES`

首版只认 `status == "banned"`，而仓里早有 `NON_TRANSACTING_USER_STATUSES = {banned, blocked, deleted}`
（[[entity_schema]]，注释明写三者「equally must not transact」；auth 中间件 / WS 闸 / admin suspend
都用它）。于是 `blocked` / `deleted` 用户的定时 job 照旧每周期入队、照旧打 401——B-13 声称修掉的
风暴在这两个状态下原样存在。现在 `_non_transacting_status(user_id)` 返回命中的状态值（否则 None），
`_poll_and_enqueue` 把**真实状态**写进 `paused_reason`（`banned` / `blocked` / `deleted`，都在
VARCHAR(32) 内），`_user_can_run` 据此短路。这些值都不匹配 `_EDGE_ONLY_RESUME_REASONS`，也不是任何
可恢复状态，所以扩值不会误触自动恢复。`inactive`（从未登录/休眠）刻意不在集合里，job 照跑。
同一条「账户不能交易」的判据从此只有一份，下次加终态（如 `frozen`）两边自动跟上。
锁：`test_poll_and_enqueue_pauses_every_non_transacting_status`（参数化三态 + paused_reason 是真实值）、
`test_user_can_run_is_false_for_blocked_and_deleted`、`test_inactive_is_a_benign_state_and_still_runs`。

## 2026-09-10（review r1 I8）— pause 通知过 `owner_notice_cooldowns` 冷却

首版 `_notify_owner_job_paused` 无条件发 inbox：auth / no_quota 这两类 `paused_reason`
不在 `_EDGE_ONLY_RESUME_REASONS`，15 分钟 backstop 只要静态 readiness 说「配置完整」就把 job
翻回 ACTIVE → 到点跑 → 同样 401 → 再暂停 → **再发一封**——一个 key 还在但已失效的用户，
按 job 调度频率无限期刷收件箱。现在用 PR #389 引入的 `OwnerNoticeCooldownRepository`
（键 `(agent_id, target=job_id, category="job_paused:<reason>")`）做去重：
- 窗口 `_PAUSE_NOTICE_COOLDOWN_SECONDS = 6h`，常量在本文件（窗口是 writer 的事，表只记时间）。
  **必须远小于** bus 侧 `NOTICE_COOLDOWN_RETENTION_DAYS`（2 天）——那条每日 sweep 删更老的行，
  窗口逼近它就会被中途重开；测试 `test_pause_notice_window_stays_well_inside_the_cooldown_retention`
  钉住比例。
- category 带 reason：`no_quota → auth` 是新事实，立刻再通知；同 reason 在窗口内不重发。
  列宽 VARCHAR(32)，`_pause_notice_category` 对所有可能的 reason（auth / no_quota / spend_cap /
  五个 self-serviceable）都 ≤ 32，参数化测试逐个钉住。
- 冷却读失败 **fail open**（宁可重复也不静默），窗口只在 inbox 写成功之后 `arm`（写失败不开空窗）。
  与 [[background_llm_alerts]] 同形。
- C1 之后 spend_cap 也成了周期性事件（每天一次），同样过这条冷却。

锁：`test_repeated_pause_for_the_same_reason_notifies_once`、`test_a_different_pause_reason_notifies_again`、
`test_notifies_again_once_the_window_has_expired`、`test_window_is_not_armed_when_the_inbox_write_fails`、
`test_cooldown_read_failure_still_notifies`。

## 2026-09-09 — B-17：job 因额度/凭据被暂停时通知 owner

`_finalize_job_execution` 把 job 标成 `PAUSED_NO_QUOTA` 那一段，之前只有
`logger.warning`——用户对「一个定时任务从此再也不跑了」**完全没有信号**，
除非自己去 Jobs 面板翻。新增 `_notify_owner_job_paused`，复用既有的 inbox
通知机制（跟 [[background_llm_alerts]] 的 `alert_agent_paused` 同一套
`InboxRepository.create_message` + `InboxMessageType.SYSTEM_NOTICE` +
`MessageSource` 写法，不是新造一条通道），在 `PAUSED_NO_QUOTA` 分支
`logger.warning` 之后调用。收件人是 `job.user_id`（JobModel 的既有语义
「接收 job 结果通知的那个人」）；`user_id` 缺失就直接跳过，不报错。

**只覆盖 no_quota/auth 暂停，不覆盖 `repeated_failure` 终态 FAILED**——B-17
的范围明确是「跟 B-12 组合：额度耗尽暂停时通知」，`repeated_failure` 是
瞬时故障用尽退避配额后的另一条终态路径，语义不同（不是"额度耗尽"而是
"这个 provider/网络一直不稳定"），未在本次范围内一并加通知。

**best-effort，从不让通知失败拖垮暂停本身**：`InboxRepository.create_message`
抛异常只记 `logger.warning`，job 该暂停照样暂停（测试
`test_notification_failure_does_not_break_the_pause` 钉死这点）。

## 2026-09-09 — B-13：banned 用户不再自动跑 job

**症状**：被封号用户的定时 job 每天照常触发，打出成堆 `Key is blocked` 401。根因是
`_user_can_run`（PAUSED_NO_QUOTA 恢复闸）和 `_poll_and_enqueue`（到期扫描）对
`users.status` **零引用**——`classify_provider_for_user` 只判「provider 能不能跑」，
账户本身被封是另一个正交事实，谁都没查过。

**修法两处**：
1. 新增 `_is_user_banned(user_id)`（2026-09-10 起改名 `_non_transacting_status`，见上）：查 `users` 表 `status == "banned"`。DB 读失败
   或行不存在都 fail **open**（返回 False）——这是刻意的：一次 DB 抖动不该让全平台
   job 集体冻结，且「banned」是显式 opt-in 状态，行不存在≠被封。
2. `_user_can_run` 把它加在 provider 分类**之前**短路返回 `False`——banned 用户不消耗
   一次 provider 探测（省一次外呼，也让「账户不可用」与「provider 不可用」两条判据
   互不掩盖）。`_poll_and_enqueue` 对每个到期 job 算出 `exec_uid = related_entity_id or
   user_id`（与 `_user_can_run` 现有口径一致），banned 就地 `update_job` 成
   `PAUSED` + `paused_reason="banned"`，**不进队列**——这样它在下一次 poll 也不会
   再被 `get_due_jobs()` 捞到（因为不再是 PENDING/ACTIVE），而不是每个周期反复判一次。

**为什么用 `PAUSED`+`paused_reason="banned"` 而不是新状态值**：`paused_reason`
本来就是自由字符串字段（见 [[job_schema]]），复用现有 `PAUSED` 语义——「不会自动
恢复，需要外部动作」——精确匹配「封号只能靠 reinstate/未来的人工操作」这件事，
且不必碰 `JobStatus` 枚举（additive-only 铁律）。这个状态天然落在
`_resume_eligible_no_quota_jobs` 只扫 `PAUSED_NO_QUOTA` 的范围之外，所以也不会被
15 分钟 backstop 误拉活。

**为什么 `rearm_user_no_quota_jobs`（登录/换 provider 触发的 edge 恢复）不用改**：
它只从鉴权中间件放行的 backend 路由触发（登录、配额变更、provider 保存），而
[[auth]] 的 account-state gate 会在到达这些路由前对 banned 账户返回 403——banned
用户物理上摸不到这条边缘触发路径，不存在绕过口。

**Swept**：`git grep -n "_user_can_run\|def _poll_and_enqueue"` 命中
`module_poller.py` 的同名方法——不同子系统（检测 Instance 完成，不代表用户执行
LLM 调用），不在本 bug 范围内，未改动。

## 2026-08-17 — `_deliver_to_origin` 降为**兜底**，主路径是 job 自己调 `message_team`

此前它是唯一路径：房间的契约是 job 的纯文本自动上墙，prompt 也这么写。那个契约没了
（见 [[step_3_agent_loop]]），所以房间版 prompt 改成让 job 调 `message_team`，主路径
和其他所有表面一致。

**当初的保证没有变**：问过的房间一定收得到回音。`has_message_from_turn` 用 event id
（不是时间窗）精确回答「这一轮有没有往那个房间放过东西」，只有答案是否时平台副本才发出。
自己发过报告的 job 不会被发第二遍；产出了报告却哪里都没送的 job，等它的四个人仍然收得到。

没有 event id 时选择投递：重复是噪音，缺失才是这个兜底存在的理由。


## 2026-08-17 — review 三条：失败也投、不投运维样板、带上溯源

**失败也要投回房间**。错误分支此前在 `_deliver_to_origin` 之前就 return 了，于
是房间**永远沉默**：没人知道它跑过、更没人知道它挂了。owner 私聊那条路有 Jobs
面板和 `job.last_error` 兜着，房间什么都没有——四个人看着有人要了个提醒，然后再
没下文。这就是本功能要治的断链，换到了 job 面；团队房自己已经为 `turn.fatal` 发
失败通知，理由同源（队友分不清「不感兴趣」和「坏了」）。

**空输出不投运维样板**。`## Task Completed … Job ID … Tools used: None` 是给
owner inbox 写的运维记录；房间不是 inbox，而且这次 run 用的提示词明确告诉模型
「你的回复就是报告，会自动上墙」。所以投的是 `room_content`（合成之前的真实产
出），owner 那条路的样板**一字未动**（PRD 验收 #8）。

**报告带 `event_id` / `root_run_id`**。job 报告是 agent 的话进入房间的**第三条**
路径，另外两条（实时回帖、巡查行）都盖了这两个戳。房间 transcript 靠 `event_id`
提供「view reasoning & tools」，而这条线**没人看见它发生**，缺了溯源就是一段没有
来路的文字。

用的是 `collection.event_id` 而不是本函数开头 `uuid4()` 生成的那个局部
`event_id`——后者不是 events 行的 id，挂上去是个悬空引用，比不挂更糟。
`root_run_id` 同值：job 执行没有父 run（叫醒它的是定时器），按
[[schema_registry]] 的定义，根 run 存自己的 event_id。

刻意不带 `mentions`：报告是通报不是请求，一个 @ 会立刻唤起一轮团队房 turn，还会
被 [[errand]] 再开一条没人交接过的差事。

## 2026-08-14 — `_deliver_to_origin`：房间来源的 job 回房间

origin 那一对的另一半：[[_job_context_builder]] 按 `job.origin_source` 选提示词，
这里按**同一个字段**选投递。空 origin 直接返回，保持历史路径（agent 在 run 内自
己调 `send_message_to_user_directly`）——PRD 验收 #8 要求私聊行为逐字不变。

**由平台以 agent 名义投**，而不是交给模型调工具：房间的契约就是明文自动上墙，
这次 run 用的提示词也正是这么说的。让模型来投等于把「它记不记得调工具」这个依
赖又请回来，而这次改动整体就是在拆掉这个依赖（铁律 #15）。

**永不抛**：job 本身**成功了**——状态、narrative、next_run_time 全都是对的——投递
失败绝不能把一个完成的 job 改写成失败并重新排期，让同一份活再跑一遍。改为大声
记 ERROR：投不出去的报告是真问题，只是不是这个 job 的失败。

## 2026-07-30 — `_EDGE_ONLY_RESUME_REASONS` 并入 `OUT_OF_CREDIT_REASONS`

原本逐个列举，其中只有 `insufficient_balance` 代表「没钱了」。免费额度用完同日拆成第二个
out-of-credit reason，若不在此集合里就会被交回**静态就绪检查 + 时间兜底**盲探 —— 而充值
并不改变配置，静态检查观察不到，于是每个周期都会重新拉起暂停的任务，正是本文件注释警告的
那场重试风暴。

改成 `*OUT_OF_CREDIT_REASONS` 展开：新的 out-of-credit reason 会自动落在这里，不依赖有人
记得回来加一行。

## 2026-07-30 — 成功执行清 last_error（否则恢复消息永久残留）

`_finalize_job_execution` 的成功分支原来只在有 backoff 时清 `consecutive_failure_count`
/ `cooldown_until` / `paused_reason`,**从不清 `last_error`**。而 `recover_all_running_jobs`
启动恢复会写 `last_error="Process restarted, auto-recovered"` 但不动 failure 计数——于是
一个恢复后每周期都成功的健康 job,UI 里会永久显示这条早已解决的错误(事故 2026-07-30,
用户改间隔后撞见)。修复:成功时**无条件**把 `last_error` 置 None(有 backoff 时再一并清
那三个字段),且仅在确有可清状态时才写库,避免每次成功多一次 UPDATE。回归测试
`tests/job_module/test_failure_backoff.py::test_success_clears_stale_last_error_without_backoff`。

## 2026-07-28 — no-quota 判定的第 1、3 层收缩

`_NO_QUOTA_ERROR_TYPES` 只剩 `NoProviderConfiguredError` /
`LLMConfigNotConfigured`；`_NO_QUOTA_ERROR_MARKERS` 只剩
`"no provider configured"`。

不是放松，是搬家：**免费额度花光已经没有专属的错误类型了**（钱包在网关上，
网关在请求路径里拒绝），它经由第 2 层 `classify_self_serviceable` 的
`insufficient_balance` 命中。第 1、3 层再留一份自己的措辞列表，就是两套分类
规则各自漂移的开始。

## 2026-07-22 — no longer its own OS process; runs under the worker supervisor

`JobTrigger.start()` is unchanged, but it is no longer launched as a standalone
`job_trigger.py` process. It is now one supervised task inside
[[run_worker_supervisor.py]] (shared event loop + DB pool, backoff-restart on
crash — which does NOT cap a running agent_loop, only re-arms the task if the
process-level loop itself raises; binding rule #14 intact). The "独立部署/独立
进程" framing below is HISTORY. Its own `ServiceAuditor("job_trigger")`,
`recover_all_running_jobs` startup recovery, and the `__main__` debug entrypoint
are retained.

## 2026-07-18 — _user_can_run docstring 随偏好删除微调（行为不变）

免费额度偏好删除（[[resolver]]）后 `_user_can_run` 的 docstring 更新
措辞——网关继续委托统一 classifier，行为零变化。注意下方 2026-06-01 条目是
**历史事故记录**：其中 `prefer_system_override`（当年是用户偏好）与
`FREE_TIER_EXHAUSTED`（判定已删）描述的是当时语义；现行判定里对应场景直接
返回 USER_OK（耗尽 + 自有 key 自动切换，job 不再卡 PAUSED）。

## 2026-07-16 — 后台 job 在"自助类"失败上暂停 + paused_reason 分流恢复

`_is_no_quota_failure` 复用 `agent_framework.llm.failure.classify_self_serviceable`
(#110 检测器,leaf util,非跨模块依赖——铁律 #3):任何**确定性自助类**失败
(余额/配额不足、上下文窗口过小、模型不存在)→ True → `PAUSED_NO_QUOTA`。

**pause 写 `paused_reason`**(用 classify 结果 / `auth` / `no_quota`),因为**恢复必须分流**:
- 时间兜底 `_resume_eligible_no_quota_jobs` 靠 `_user_can_run`(=配置完整性,**看不到余额**)。
  余额=0 的用户配置是完整的 → 会被判"可运行" → 若盲目 re-arm,就是每轮翻回→再失败→再暂停的
  **重试风暴**(这正是我第一版的 bug:把 dev 的"8 次后 FAILED 终止"改成了"永久探")。
- 所以兜底**跳过** `_EDGE_ONLY_RESUME_REASONS`(insufficient_balance / context_window /
  model_not_found)——这些的修复 readiness 观察不到(充值不改配置、换模型才改),**只在真实
  边缘恢复**:provider/slot 重配(`rearm_user_no_quota_jobs`,清 paused_reason)或手动。
- auth / 遗留 quota **不在**该集合:重配 key 会改配置,readiness 能观察到,保持原有兜底恢复。

恢复语义(PR #116 review 后完善):边缘路径 `rearm_user_no_quota_jobs`(每次登录 + provider
保存都触发)走 `ProviderReadiness.validate` 的**实测**;`provider_registry._interpret_test_response`
已修——余额/模型/上下文的 400/404 不再被当"auth 通过=可达",而是复用 `classify_self_serviceable`
判为**not ready**。于是:仍没钱 → 实测 not-ready → job 保持暂停(**不白跑一次**);充值后 →
实测 200 → 恢复。所以**充值后下次登录即自动恢复且准确**,是"真止损 + 准确恢复",不是"15 分钟盲探"。
裸 429/限流仍是 transient。差异见 `.mindflow/project/references/netmind_billing.md`。

## 2026-07-13 — auth failures are recoverable, not terminal + zombie self-heal

Incident: a daily cron job hit "Claude API authentication failed", which
`_is_no_quota_failure` did NOT match, so it took the transient path
(COOLING → after `_MAX_CONSECUTIVE_FAILURES` → terminal **FAILED**). FAILED has
no recovery scan, so the job stayed dead even after the owner fixed auth.

Fix 1 — new `_is_auth_failure(result)` (error_type `auth_expired` /
`AuthenticationError` / … or message markers like "authentication failed",
"not logged in", "401"). `_finalize_job_execution` now routes
`_is_no_quota_failure(result) OR _is_auth_failure(result)` to
`PAUSED_NO_QUOTA` — the "provider/credentials unusable" pause — so the readiness
backstop (`_resume_eligible_no_quota_jobs`, 15min) revives it once auth works.
Auth failures therefore NEVER reach terminal FAILED. (Conceptually
`PAUSED_NO_QUOTA` now means "provider config OR credentials unusable"; the
`error_message` carries the specific reason. The enum value is unchanged — no
DB semantics change.)

Fix 3 — new `_heal_unscheduled_active_jobs()`, run in the same 15min backstop
gate. It finds ACTIVE scheduled/ongoing jobs with a NULL `next_run_time` (which
`get_due_jobs`, `WHERE next_run_time <= now`, can never select → "active but
never runs") and recomputes `next_run`. Belt-and-suspenders behind the
reactivation fix in `job_service.update_job`. Repo query:
`get_active_scheduled_jobs_missing_next_run`.

## 2026-06-01 — edge recovery backstop + long-running diagnostic (batch ②b)

Poll step 1 no longer force-recovers RUNNING jobs older than 30min
(`recover_stuck_jobs`) — that would interrupt a legitimate long agent_loop AND
duplicate it (铁律 #14). Replaced with `_diagnose_long_running_jobs()` (via
`repo.find_long_running_jobs`, read-only): it logs a WARNING per long-runner for
alerting but never resets them. Orphan RUNNING rows from a killed process are
still reset at startup by `recover_all_running_jobs` (the only safe time).

Poll step 1.5 (`_resume_eligible_no_quota_jobs`) is now gated to a low-frequency
backstop (`_NO_QUOTA_BACKSTOP_INTERVAL_S`, 15min) instead of running every 60s
cycle. Primary PAUSED_NO_QUOTA recovery is edge-triggered from the
provider-mutation routes via `job_recovery.rearm_user_no_quota_jobs`.

NOTE: `find_long_running_jobs` filters in Python, not via a SQL datetime-string
comparison — SQLite stores datetimes with a 'T' separator but binds a datetime
param with a space, so `started_at < %s` compares wrong ('T' > ' '). This is a
latent SQLite-only bug `get_due_jobs` / `recover_stuck_jobs` share (masked by
native MySQL DATETIME in prod); Python filtering is correct on both backends.

## 2026-06-01 — transient-failure backoff via COOLING (batch ②)

Before: `_finalize_job_execution` ignored `success` on the non-quota path, so a
run that *failed* for a transient reason (network / 5xx / timeout) rescheduled
straight to ACTIVE and re-fired every interval; a one_off failure was even
marked COMPLETED (hiding the failure). Now a non-quota failure goes to `COOLING`
with `cooldown_until = now + _compute_cooldown_seconds(n)` (exp backoff: 60s ×2,
cap 1h) and `next_run_time = cooldown_until`; `consecutive_failure_count` is
incremented; at `_MAX_CONSECUTIVE_FAILURES` (8) it escalates to `FAILED`
(`paused_reason=repeated_failure`) instead of cooling forever. A success resets
the counter and clears cooldown before the normal complete/reschedule branch.

`_rearm_cooled_jobs()` (poll step 1.6, symmetric with the no-quota resume step)
flips `COOLING → ACTIVE` once `cooldown_until <= now` — **time-based** recovery,
so polling is the natural trigger (contrast PAUSED_NO_QUOTA, which is
state-change/edge recovered). A COOLING job's instance status is deliberately
left untouched (not terminal) so dependents stay blocked until it finally
succeeds or gives up.

铁律 #14: this spaces SCHEDULER retries, never caps a running agent_loop — only
a run that finished AND failed accrues backoff. New `instance_jobs` columns
(`consecutive_failure_count`, `cooldown_until`, `paused_reason`, `paused_at`)
are additive (auto_migrate); JobStatus gains `COOLING` / `BLOCKED` /
`BLOCKED_FAILED` (code-only, VARCHAR column unchanged — 铁律 #6).

## 2026-06-01 — resume gate unified onto the single provider classifier (oscillation fix)

The 2026-05-22 resume gate `_user_can_run` reimplemented the provider decision
tree as "`QuotaService.check()` OR own-provider-complete". That **ignored
`prefer_system_override`** and so disagreed with the runtime: a user opted in to
the free tier (`prefer_system_override=1`, the default) whose quota was
exhausted but who *also* had a complete own provider was judged "can run" →
resumed `PAUSED_NO_QUOTA → ACTIVE` → picked up → runtime routed to the exhausted
free tier and raised `SystemDefaultUnavailable` (it will NOT silently fall back
to the user's own key) → re-paused. Every poll cycle. Prod 2026-05-31 logged
~1828 pause / ~1826 resume over 72h for 4 such jobs (elricwan, haili, two test
users — all `prefer_system_override=1` + exhausted + own provider).

Fix: `_user_can_run` now delegates to `provider_resolver.classify_provider_for_user`
(→ `ProviderResolver.classify` → `ProviderAvailability`) and returns
`is_runnable(verdict)`. The resume gate, the HTTP path (`resolve`) and — by
construction — the runtime now share ONE classifier, so they cannot drift again.
For the regression case the verdict was `FREE_TIER_EXHAUSTED` → `is_runnable`
False → the job stayed `PAUSED_NO_QUOTA` until the user acted. **(历史语义：
该判定已于 2026-07-18 删除,同场景现判 USER_OK 直接恢复;护栏测试
test_user_can_run_uses_classifier.py 曾因引用已删枚举被 except 吞掉而空转
——PR #121 review 抓出,已改写为钉"委托本身"+ 各现存判定,elricwan 场景
改断言 runnable。)** On any classifier error the gate is conservatively
False (don't resume into an unknown state).

铁律 #15 still honoured: opted-out own-provider users pass via `USER_OK`; the
platform never overrides the user's choice — it only stops resuming a job into a
run the runtime is guaranteed to refuse.

(Job-scheduler resilience redesign batch ①, 2026-06-01. Remaining batches — cooling/backoff, edge-triggered recovery, pause/resume
API + notifications + frontend — are not yet implemented.)

## 2026-05-22 — no-quota auto-pause + resume (#6 infinite-loop fix)

A run that failed because the owner's free-tier quota is exhausted (and no own
provider is configured) returns `success=False` (it does NOT raise), so it
bypassed `_handle_job_failure` and went through `_finalize_job_execution`, which
ignored `success` and **rescheduled** the recurring/ongoing job — so it re-fired
every interval into the same wall forever (amplified by many jobs).

Fix:
- `_finalize_job_execution` now early-returns BEFORE the reschedule branching
  when `_is_no_quota_failure(result)` (error_type ∈ `_NO_QUOTA_ERROR_TYPES`
  like `QuotaExceededError`, set by `step_3_agent_loop`'s
  `error_type = type(e).__name__`; plus a message-substring fallback). It sets
  status `PAUSED_NO_QUOTA` and does not reschedule. **Transient** failures
  (network/LLM hiccups) are deliberately excluded → they still reschedule.
- `_poll_and_enqueue` calls `_resume_eligible_no_quota_jobs()` each cycle:
  for every `PAUSED_NO_QUOTA` job, `_user_can_run(uid)` checks system quota
  (`QuotaService.default().check`) OR a complete own provider
  (`UserProviderService` + `_is_user_config_complete`); if so, recompute
  `next_run` from now and flip back to ACTIVE. Covers both resume triggers
  (quota topped up / own provider configured).

铁律 #14: this is purely job-scheduler-level — no agent_loop time/iteration
limit, no force-stop. #15: own-provider users' runs succeed, so they never enter
the pause branch and their jobs are never wrongly paused.

## 2026-04-27 — disable per-run file logging (fd-leak fix)

EC2 production observation: `narranexus-jobs` Python process saturated
file descriptors at 1021 / 1024 limit after ~3 days of uptime, and
**every subsequent job run failed with `OSError: [Errno 24] Too many
open files`** thrown from `logging_service.py:128`. Of the 1021 fd, 674
were `PIPE` — roughly 337 unreclaimed `multiprocessing.SimpleQueue`
instances created by loguru's `enqueue=True` worker queue.

Root cause: `LoggingService.setup()` calls `logger.add(..., enqueue=True)`,
which spawns a `multiprocessing.SimpleQueue` (2 pipe fd + 1 lock fd).
Cleanup is owned by the agent_runtime background hook task; if `setup`
itself raises (e.g. fd exhaustion), or the BG task is killed before its
finally clause runs, the queue's fds are never closed. JobTrigger runs
high-frequency cron jobs (e.g. S&P 500 every 10 min — 144 runs/day),
so the leak compounds fastest here. `narranexus-backend`, which also
uses default `LoggingService`, leaks at a much lower rate; `lark_trigger`
and `message_bus_trigger` already disable file logging entirely.

Fix: pass `LoggingService(enabled=False)` to `AgentRuntime` in
`_run_agent`, matching the convention already used by `lark_trigger.py`
(line 1242) and `message_bus_trigger.py` (line 409). With logging
disabled, `setup()` returns immediately without allocating a loguru
handler, so the leak path is closed.

Trade-offs:
- Per-agent log files at `~/.narranexus/logs/agents/<agent_id>_*.log`
  are no longer written for job-triggered runs. Same trade-off
  `lark_trigger` and `message_bus_trigger` already accepted.
- `docker logs narranexus-jobs` still surfaces full loguru output to
  stdout, so post-incident triage is unaffected. Container log retention
  is the operational source of truth for trigger-run history.
- The deeper fix (remove `enqueue=True` from `LoggingService` itself or
  redesign cleanup ownership) remains open for the architectural
  TODO list — this fix is the smallest change that aligns the three
  trigger processes and stops the bleed.

## 2026-04-20 — runtime consumption via `collect_run` (Bug 2)

Inner loop now delegates to `agent_runtime.run_collector.collect_run`.
When `collection.is_error` is true the returned job result carries
`success=False`, `error_type`, and `error_message` — replacing the old
misleading "Task executed but produced no text output" fallback for
runs that actually errored (e.g. owner removed their provider, system
quota exhausted). Downstream `_finalize_job_execution` persists the
real failure reason on the job row.

# job_trigger.py — Job 后台轮询执行服务

## 为什么存在

`JobTrigger` 是 Agent 系统的"时钟"——它独立运行，持续扫描到期的 Job 并触发执行。没有它，所有 Job 只能在用户主动发消息时被动执行；有了它，Agent 才能在深夜执行定时任务、在约定时间自动跟进。

这是系统里唯一需要独立部署的 Module 组件，通过 `make dev-poller` 启动。

## 上下游关系

- **被谁用**：`run.sh` / `Makefile` 通过 `python -m narranexus_plugins.job_module.job_trigger` 直接启动；Tauri desktop 通过 sidecar 启动
- **依赖谁**：`AgentRuntime`（懒加载，避免循环引用）执行 Job；`JobRepository.try_acquire_job()`（原子锁）防重复执行；`_job_context_builder.build_execution_prompt()`；`_job_scheduling.calculate_next_run_time()`；`UserRepository`（获取用户时区用于 cron 计算）

## 收事件方式

**Worker Pool 模式**：1 个 Poller 协程 + N 个 Worker 协程（默认 5）。Poller 每 60 秒扫一次 DB 找到期 Job，通过 `asyncio.Queue` 送给 Worker。`_running_jobs: Set[str]` 防止同一 Job 被多次入队。

**原子锁防重复**：`try_acquire_job()` 用数据库原子 UPDATE 把状态从 `PENDING/ACTIVE → RUNNING`，只有成功的 Worker 才能执行。这解决了多实例部署（未来）或 Worker Pool 内竞争的重复执行问题。

## 执行身份切换

`_execute_job()` 里用 `job.related_entity_id or job.user_id` 作为执行时的 `user_id` 传给 `AgentRuntime`。这让针对特定用户的 Job（如销售跟进任务）在执行时加载**目标用户**的 Narrative 和社交图谱，而不是 Job 创建者的上下文。

## 设计决策

**`_finalize_job_execution` 的 ONGOING 处理**：ONGOING Job 完成一次执行后，优先由 `after_turn`（入口 1，LLM 分析）决定下次执行时间和状态；`job_trigger` 只更新 `iteration_count`，并在入口 1 失败（状态仍为 RUNNING）时作为 fallback 机械更新。两入口的协调通过数据库状态判断，没有显式锁。

**启动恢复**：服务启动时调用 `repo.recover_all_running_jobs()` 把所有 `RUNNING` 状态的 Job 恢复为可调度状态，避免上次进程被杀后 Job 永久卡在 `RUNNING`。

## Gotcha / 边界情况

- **Schema 自动迁移**：`start()` 里调用 `auto_migrate()` 确保所有表存在。这是 JobTrigger 作为独立进程启动时不依赖主进程初始化的必要措施。
- **用户时区影响 cron 执行时间**：cron 表达式按用户的本地时区解释，需要通过 `UserRepository.get_user_timezone()` 获取用户设置的时区（IANA 格式）。时区获取失败时 fallback 到 UTC，这可能导致 cron 任务在错误的时间执行。

## 新人易踩的坑

- 在 SQLite 环境下运行多个 JobTrigger 进程（不应该，但可能误操作）会因 SQLite 单写锁导致 `try_acquire_job()` 的 UPDATE 语句死锁。
- `AgentRuntime` 是懒加载（`from narranexus.platform.agent_runtime import AgentRuntime`），这是避免循环导入的必要措施——不要改成模块顶部导入。

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

## 2026-08-19 — SCHEDULED finalize：end_at 地平线完结 + in-run 状态重读

`_finalize_job_execution` 的 SCHEDULED 分支在 update_last_run 之后、任何
调度写之前做两件事（顺序承重）：

1. **重读当前 status（对齐 ONGOING 分支的既有做法）**：`job` 是执行前快照；
   agent 可能在本次运行中 `job_update(status='paused')` 自暂停（onboarding
   引导的"别再找我"就靠这条），用户也可能在运行期间从 Jobs 面板暂停/取消。
   命中显式终止集（PAUSED / PAUSED_NO_QUOTA / CANCELLED / COMPLETED /
   FAILED）→ 只补 instance completed（run 完成是每次运行的事实，与调度
   无关）+ return，不写 ACTIVE、不写 next_run；**其余状态（RUNNING，以及
   测试 harness 直调时的 pending/active）照旧重排**——用"!= RUNNING"当
   判据会把 harness 直调的 pending 行也 respect 掉（S2b 时区测试实证）。
   这也保证地平线分支不会拿 COMPLETED 盖掉运行中的 CANCELLED。last_run 在
   重读之前写——它是事实记录，任何状态下都该落。
2. **end_at 地平线**：`past_schedule_horizon(trigger_config, next_run.utc)`
   越线 → clear next_run + COMPLETED + instance completed + return——平台
   强制"这个日程排到 X 日为止"，不依赖模型自觉。**只有地平线路径会完结**：
   next_run 为 None 的历史语义（ACTIVE + NULL next_run）原样保留。

重武装侧门同样接了地平线（否则"排到 X 日"会从失败重试/僵尸自愈漏一次
fire）：`_rearm_cooled_jobs` 对退火完成但重试时刻已越线的 COOLING job 直接
完结；`_heal_unscheduled_active_jobs` 对重算 next_run 已越线的僵尸完结而非
复活。**已知有界缺口（有意保留，round-5 review B 补全枚举）**：4 个恢复路径
写 next_run 但不查地平线——`job_recovery.rearm_user_no_quota_jobs`（登录/额度
恢复边缘）、`resume_job`（用户手动恢复）、`reschedule_job`（用户改执行时间）、
`_resume_eligible_no_quota_jobs`（15min backstop）。都是复活 PAUSED 态 job 的
恢复路径，最坏多跑一次，随后 finalize 越线完结（finalize 是权威兜底）；不给
它们加守卫是因为完结一个从未在本轮运行的 PAUSED 态 job 与 finalize 的 instance
完结语义不同（PAUSED 态 instance 非 in_progress），加守卫是另一个设计决定、需
各配测试。恢复面核对过：job_recovery 只复活 PAUSED_NO_QUOTA，不会把 COMPLETED
拉回来。

测试：tests/job_module/test_schedule_horizon.py（越线完结 / 未越线照常 /
无地平线逐字不变 / 自暂停不被复活 / CANCELLED 不被 COMPLETED 覆盖 /
COOLING 与僵尸两条侧门的越线+无地平线对照）。

## 2026-08-19（三轮）— 地平线推广到 ONGOING；in-run stop 清 next_run

- **ONGOING 两条路径都接地平线**：end_at 随 MCP schema 开放给模型后，
  三种 job_type 必须一句话说清语义——"recurring（scheduled/ongoing）认
  地平线，one_off 忽略"。ONGOING 有两支：①hook 失败的机械回退支（status
  仍 RUNNING）自算 next_run 后查越线；②hook 接管的 else 支——**四轮 review
  补**，因为 hook 正常接管才是常走路径，只补机械回退支等于只在 hook 失败
  时才刹车。else 支不覆盖 hook 的语义决策（end_condition 由 LLM 判），只查
  hook 重排的 `current_job.next_run_time` 是否越线：非空且越线 → COMPLETED
  + clear_next_run + instance completed；hook 已完结（COMPLETED/FAILED）时
  next_run 已清空为 None → 跳过、绝不二次完结（防重复触发 instance
  completion）。next_run_time 从 DB 回来可能 naive，先补 tzinfo=utc 再比
  （同 _rearm_cooled_jobs）。iteration_count 只在机械回退支前写一次，else
  支不重写。**`_IN_RUN_STOPS` 守卫（round-5 review 🟢2）**：越线判断前先看
  `current_status not in _IN_RUN_STOPS`——in-run stop（用户 Jobs 面板暂停/
  取消，或 hook 写了 PAUSED/CANCELLED 等终止态）拥有结局，否则残留/hook 重排
  的越线 next_run 会把运行中被 cancel 的 job 盖成 COMPLETED（SCHEDULED 分支
  一直有同款守卫）。这条 respect 路径**不**补 per-run instance completed（与
  SCHEDULED 不同，ONGOING 有意如此）。测试：test_schedule_horizon.py 的
  hook_reschedule_past_horizon_completes（越线完结，删 else 支判断必红）/
  hook_reschedule_before_horizon_is_respected（未越线保持 active）/
  hook_completed_is_not_double_completed（None next_run 跳过，spy
  _update_instance_completed 断言零调用，不再空断言）/
  hook_reschedule_respects_inrun_cancel（in-run CANCELLED 不被 COMPLETED 覆盖，
  删 _IN_RUN_STOPS 守卫必红）。
- **`_rearm_cooled_jobs` 两处收口**：加 `job_type != ONE_OFF` 守卫（one_off
  没有"下一次 fire"可bound，越线完结会把一个从未送达的一次性提醒标成
  completed）；重试时刻用 `max(cu, now)`——停机数天后 cu 早于地平线而 now
  已越线时，用 cu 判会把本要堵的"多一次 fire"从停机恢复路径漏回去。
- **in-run stop 分支补 `clear_next_run`**：本文件其余四条终态路径都清
  next_run，try_acquire_job 不清——不补的话运行中被 cancel/pause 的 job
  行上留着过期 next_run_time（poller 按 status 过滤不受影响，纯脏数据）。
  测试 fixture 先种一个非空 next_run 再断言清空，避免对 NULL 空断言。
- `_IN_RUN_STOPS` 提到模块级（_MAX_CONSECUTIVE_FAILURES 先例），供后续
  分支复用同一份判据。
- 测试：test_schedule_horizon.py 增 ONGOING 越线完结 / ONGOING 无地平线
  照旧 / ONE_OFF 在 COOLING 忽略地平线三条对照。
