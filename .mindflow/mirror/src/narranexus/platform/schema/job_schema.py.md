---
code_file: src/narranexus/platform/schema/job_schema.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r3 I1）— `SUSPENDABLE_JOB_STATUSES`：封号暂停的状态集合

新元组 `(PENDING, ACTIVE, COOLING)`——调度器会自己启动的状态。[[job_repository]] 的
`pause_jobs_for_execution_principal` 只读这一份。刻意不含 BLOCKED / BLOCKED_FAILED（解封会被恢复成
ACTIVE、依赖未满足就开跑）、RUNNING（归在飞 run 的 finalize）、PAUSED_NO_QUOTA / PAUSED_SPEND_CAP /
PAUSED（保留自己的 reason）与终态。与 `LIVE_JOB_STATUSES` 是两个不同问题的集合，不要合并。

## 2026-09-10（review r1 I11/M1）— `TriggerConfig.TIME_BEARING_FIELDS` 唯一清单；空串 timezone 不再被「修好」

「哪些字段算 time-bearing」此前有三份副本：写侧 validator 的四个 `is not None`、
`from_stored_dict` 的元组、[[job_recovery]] 的 `_TIME_FIELDS`。B-15 修的正是「写侧学了新字段、
读侧没跟上」在老数据上炸——留三份副本等于预约第二次。现在 `TIME_BEARING_FIELDS: ClassVar`
（与 `MAX_INTERVAL_SECONDS` 同款公开 ClassVar）是唯一清单：validator 用 `getattr`、
`from_stored_dict` 用 `data.get` 遍历同一个元组；job_recovery 的可编辑集合从它派生（去掉
`end_at`、加 `timezone`，差异写在那边注释里）。
M1：`from_stored_dict` 的缺失判定从 `not data.get("timezone")` 改成 `is None`——显式 `timezone: ""`
是「存在但坏」的值，该由 `timezone_must_be_iana` 报错，而不是被悄悄换成 UTC（与 docstring
「only fills in an ABSENT value, never repairs a bad one」一致）。
锁：`TestTimeBearingFieldsSingleList`（对 ClassVar 每个字段参数化：构造器必须要 timezone、
`from_stored_dict` 必须补 UTC；空串必炸；显式 None 视为缺失）。

## 2026-09-10（review r1 I5）— `LIVE_JOB_STATUSES`：「会自己再跑」的状态集合只定义一次

B-16 让 `BLOCKED` 第一次真正可达，而 [[job_repository]] 四个「活跃 job」查询（同名重复检测 /
相似标题确认门 / 按 narrative 列既有 job / agent prompt 的「我有哪些 job」）各自手写
`('pending','active'[,'running'])`——一条等依赖的 job 对 LLM 完全隐形，用户再说一遍同样的需求就
直接建出第二条。`COOLING` 有同样的洞（退避中、会自己重试，却不算「已有」）。现在
`LIVE_JOB_STATUSES = (pending, active, running, blocked, cooling)` 是这四处唯一的真源：语义是
「已排期 / 在跑 / 无需 owner 动作就会再跑」。paused 三态刻意不在（等 owner 或平台决策，恢复时
重算排期）；终态不在；**`get_due_jobs()` 不用它**——到期扫描必须只选 PENDING/ACTIVE，那正是 B-16
的修复本体（`test_due_poll_is_untouched_by_the_live_set` 钉住）。
`find_active_by_title` 顺带纳入 `running`：一条正在跑的 job 被重复下单时返回既有 job 而不是再建一条。

## 2026-09-10（review r1 C1）— `PAUSED_SPEND_CAP` 的真实恢复语义

上一节首版写的「不被任何 backstop 自动拉活、只能手动恢复」两句都不成立——那时它既不在
`_RESUMABLE_STATUSES` 也没有任何扫描，是彻底的死路。现在的真实行为（注释同步改）：
[[job_trigger]] 的 15 分钟 backstop `_resume_spend_capped_jobs` 在用户当地次日（或 cap
被调低/关闭）自动恢复；[[job_recovery]] 的 `resume_job` 也接受它（Jobs 面板 Resume 按钮）。
新增枚举值的**全部消费面**这次一起登记：前端 `JobStatus` / `JobQueueStatus` / `QueueCounts`
（[[api.ts]]）、[[jobStatusVisuals.ts]] `VISUALS`、[[jobsPanelModel.ts]] `ATTENTION_STATUSES` +
`STATUS_ORDER`、[[JobsPanel.tsx]] `canResume`、看板 [[_helpers.py]] `_LIVE_JOB_STATES` /
[[_schema.py]] `QueueCounts` + `queue_status` Literal、10 份 locale 的 `jobs.status.pausedSpendCap`。
`tests/backend/test_dashboard_live_job_states.py` 把「`_LIVE_JOB_STATES` = 全部非终态 JobStatus」
钉死，下一个新状态漏登记会在那里红。

## 2026-09-10（review r1 I7）— 撤掉 `max_tokens_per_run`

B-14 首版给 `TriggerConfig` 加过 `max_tokens_per_run: Optional[int]`，
[[job_trigger]] 把它塞进 `trigger_extra_data` 透传。复审查明它是**死字段**：
`trigger_extra_data` 的全部消费方（`openai_compat` / `manyfold/sync` /
`websocket`）没有一个读它，也没有任何 route / MCP 工具 / 前端能设置它——但
Field description 写着「cap on total tokens … Guards against …」，读到的人
（包括通过自由 dict 写 `trigger_config` 的 LLM）会以为花费被限住了。而真要
「兑现」它就得给 agent_loop 加硬上限，直接撞铁律 #14——这个接口永远无法兑现，
却要永远维护（YAGNI / 铁律 #2）。整个字段连同它的三条测试一并删除；B-14 的
花费闸（`PAUSED_SPEND_CAP`）本身是完整、独立成立的，不受影响。

## 2026-09-09 — B-14：`JobStatus.PAUSED_SPEND_CAP`

一个用户在两个 2 小时心跳 `ongoing` job 上 4 天烧了约 $140（单次运行
1-7M input tokens）——job 层此前对「一天到底花了多少钱」完全没有上限。
additive-only 新增：

- `JobStatus.PAUSED_SPEND_CAP = "paused_spend_cap"`——用户当日**全部** LLM 花费（读
  `cost_records`，不止 job；「当日」按 job 冻结时区，见 [[job_trigger]] 2026-09-10 条）在
  下一次调度开始**之前**已达/超过 `NARRANEXUS_USER_DAILY_SPEND_CAP_USD` 时暂停 job。跟 `PAUSED_NO_QUOTA` 不
  是一回事：不被任何 backstop 自动拉活（花费上限是有意的天花板，不是瞬时
  条件），恢复只能靠手动或等次日花费自然清零重新判定。纯字符串枚举值新增，
  不碰 `instance_jobs.status` 列的类型/宽度，additive。

## 2026-09-09 — B-15：`TriggerConfig.from_stored_dict` —— 只对读路径宽容

`timezone_required_for_time_bearing_triggers`（2026-04-21 上线）之前写入的行——
比如裸 `{'cron': '0 13 * * 1-5'}`，完全没有 `timezone` 键——用严格构造器
`TriggerConfig(**data)` 重建时必炸 `ValidationError`。`_load_related_jobs_context`
([[job_module]]) 就是这样悄悄失败的：`JobRepository.get_job` 内部重建
`TriggerConfig` 时炸掉，异常被外层宽 `except` 吞掉，整批 related-job 上下文
静默消失。

**新增 `TriggerConfig.from_stored_dict(data)`**：只在「存在 time-bearing 字段
但 `timezone` 缺失」时，在**内存里**补一个 `"UTC"` 默认值再构造；`timezone`
本来就存在（哪怕是错的，如 `"CST"`）一律原样传给严格构造器——校验该炸还是炸，
这个方法只补「缺失」，不修「错误」。**绝不回写数据库**——旧行永远保持它写入
时的原样，下次读到还是同一个待补默认值的状态,不会有第二个进程静默"修数据"。

**为什么不放宽 `timezone_required_for_time_bearing_triggers` 本身**：那个
validator 是**写路径**的门（job 创建/更新、`job_update` MCP 工具、
`instance_sync_service` 的自动装配、`job_recovery.reschedule_job`）——所有这些
都必须继续强制新/改的 job 显式给 timezone，`TestTriggerConfigTimezoneRequired`
钉死这个行为。放宽它等于让新代码也悄悄退回不写 timezone 的坏习惯。

**Swept**：`git grep "TriggerConfig(\*\*"` 命中的另外 4 处
（[[_job_writes]] 的 `update_job_from_args`、[[job_recovery]] 的
`reschedule_job`、[[job_service]] 的创建路径、
`services/instance_sync_service.py` 的自动装配）全部是**写路径**——重建的对象
即将被持久化或立即拿去调度，都必须保留严格校验，未改动。**只有**
[[job_repository]] 的两处**读路径**（`_row_to_entity` / `recover_stuck_jobs`
的 next_run 重算）换成了宽容构造器，因为它们重建的是已经存在、无法追溯改写的
历史行。

## 2026-08-14 — `JobOrigin` + 两个 origin 字段

job 记住它是在哪儿被要求的。此前 job 只记得**做什么**，忘了**在哪被问**——于是
在团队房里当着四个人要的「明早提醒我们」，投进了 owner 的私聊，问的那个房间再
没收到过回音。

`JobOrigin` 刻意是**小的闭集**而不是「任意 WorkingSource」：每个值都需要真实存在
的投递代码，一个能记录却投不出去的来源比不记录更糟——执行时会把答案路由进一个
静默什么都不做的分支。

`MESSAGE_BUS` 目前特指**团队房**。peer DM 不在内：agent 对 agent 的频道没有人类
读者，往那儿投的报告等于没人看见，owner 私聊才是它诚实的归宿。

## 2026-08-10 (PR-8b r2) — `JobUpdateFields`：job_update 可变字段的单一来源

新增 `JobUpdateFields`(9 个 optional 字段：title/description/payload/guidance_text/
trigger_config/job_type/next_run_time/status/related_entity_id)。存在理由：job_update
的字段清单原本手抄 4 份——`update_job_from_args` keyword 签名、MCP 工具 `fields` 字面量、
前端 `JobUpdateBody`、seam `JobUpdateSeamBody`。schema 层两份 body 现都 derive 自本类
（前端加 `agent_id`，seam 加 `extra="forbid"`），字段只声明一次。**parity 承重**：seam
是 HttpStore 写路径，pydantic 默认 `extra="ignore"` 会让「加字段漏改 body」在云上静默丢字段
（success=True 但少写）而本地 DirectStore 却写入=seam byte-parity 要防的分叉；forbid 让漂移
以 422 响亮失败。None=不改语义见 [[_job_writes]] `update_job_from_args`。放 schema 包因它是
工具契约(agent-side)，两个 backend 路由 import 它属允许方向(backend→agent，铁律 #21)。

## 2026-06-01 — resilience states + backoff fields (batch ②)

`JobStatus` gains three values, modelling "non-terminal, can't run now, blocked
on a specific guard" (recovery = that guard re-passing, all arbitrated in
JobTrigger): `COOLING` (transient-failure backoff; time-recovered), `BLOCKED`
(unmet job dependencies), `BLOCKED_FAILED` (a prerequisite FAILED and policy is
"block"). `JobModel` gains `consecutive_failure_count`, `cooldown_until`,
`paused_reason` (no_quota / repeated_failure / dependency_failed / user),
`paused_at`. All additive (enum strings + nullable/default columns) → 铁律 #6
safe. See `job_trigger.py` for the state machine and
`2026-06-01-job-scheduler-resilience-design.md`.

## 2026-05-22 — JobStatus.PAUSED_NO_QUOTA (#6)

New status value `paused_no_quota`: a recurring/ongoing job whose run failed
because the owner's free-tier quota is exhausted (and no own provider) is parked
here instead of being rescheduled (which caused the every-interval infinite-loop
re-fire). Distinct from the reserved generic `PAUSED` so the frontend can label
it "No quota" and JobTrigger's recheck can target it for auto-resume. Additive
enum value (string) → safe per 铁律 #6. Mirrors: `job_trigger.py` (pause/resume),
`JobsPanel.tsx` + `api.ts` (frontend label).

# job_schema.py

## Why it exists

Background tasks (Jobs) are a first-class concept in NexusAgent — they allow the agent to do work on the user's behalf on a schedule or with a delay, without blocking real-time conversation. This file defines the entire data contract for that system: how jobs are described (`JobModel`), how triggers are configured (`TriggerConfig`), and how the LLM reports back what happened after each execution (`JobExecutionResult`, `OngoingExecutionResult`).

**v2 timezone protocol (2026-04-21):** `TriggerConfig` now enforces a `timezone` field for all time-bearing triggers. `run_at` is strictly naive (no tzinfo). IANA validation is performed via `zoneinfo.ZoneInfo`. This is the v2 timezone protocol per spec `2026-04-21-job-timezone-redesign-design.md`.

**`TriggerConfig.immediate()` (2026-06-01):** canonical "fire now" one_off trigger — `run_at` = current UTC wall-clock as a naive datetime + `timezone="UTC"`. Added because `/api/jobs/complex` hand-built `{"trigger_type":"immediate","run_at":utc_now()}`, violating the contract three ways (no such field `trigger_type`; aware `run_at` rejected by `run_at_must_be_naive`; missing `timezone`) so that endpoint failed every time. Always use `immediate()` instead of hand-rolling an immediate-trigger dict.

## Upstream / Downstream

`JobRepository` persists and loads `JobModel`. `JobTrigger` (background service) reads due jobs from the repository and fires them through `AgentRuntime`. `JobModule.after_turn()` receives the `PathExecutionResult`, asks the LLM to produce a `JobExecutionResult` (or `OngoingExecutionResult` for ONGOING type), then writes that back to the database via `JobRepository`. The frontend Job panel reads `JobModel` data through `api_schema.JobResponse`.

## Design decisions

**Three job types: `ONE_OFF`, `SCHEDULED`, `ONGOING`**: the first two cover standard task scheduling. `ONGOING` was added in January 2026 for polling/monitoring scenarios (e.g., "keep checking until the customer replies"). ONGOING jobs combine `interval_seconds` with a natural-language `end_condition` that the LLM evaluates after each execution.

**`payload` is natural language, not structured parameters**: the execution instruction is a free-form string assembled into a prompt by `JobTrigger`. This was chosen over structured function calls because different agents have different tool sets and the LLM can interpret intent better from natural language than from rigid parameter schemas.

**`clamp_interval_seconds` validator with a 90-day cap**: LLMs occasionally generate unreasonably large interval values (e.g., scheduling a task "in one year"). The validator silently clamps to 90 days (7,776,000 seconds). Similarly, `clamp_next_run_time` in `JobExecutionResult` caps the next run to 90 days in the future. These guards prevent runaway scheduling.

**`JobExecutionResult` is separate from `JobModel`**: it is a lightweight LLM output struct containing only the fields the LLM needs to fill in after execution. Reusing `JobModel` would expose system management fields (embedding, instance_id, etc.) to the LLM prompt unnecessarily.

**`related_entity_id`** makes the Job execution use a specific user's context. When set, `JobTrigger` loads that user's Narrative and social graph instead of the job creator's context. This enables scenarios like "Agent monitors customer X on behalf of the creator".

## Gotchas

**`JobModel.process` is a list of strings**: it is an append-only execution journal, not a status field. Each run adds 2-5 natural-language step descriptions. Over time this list grows unboundedly. There is no automatic truncation — if a SCHEDULED job runs daily for a year, `process` will have 365+ entries.

**`JobStatus.RUNNING`** is set by `JobTrigger` at execution start and should be cleared to `ACTIVE` or `COMPLETED` when execution finishes. If the process dies mid-execution, the job stays `RUNNING` forever. There is a `started_at` field intended for timeout detection, but as of this writing no automatic stuck-job recovery is implemented.

**`TriggerConfig.cron`** is a standard 5-field cron expression but there is no validation of the expression format. An invalid cron string (e.g., `"0 8 * * * *"` with 6 fields) will be stored successfully and then silently fail to parse at execution time.

**`TriggerConfig.timezone` is now required for all time-bearing triggers**: When you construct a `TriggerConfig` with `run_at`, `cron`, or `interval_seconds` without a `timezone`, Pydantic raises a `ValidationError`. The error message says "timezone is required when run_at / cron / interval_seconds is set." This catches LLM-generated tool calls that omit the timezone field; the MCP agentic loop will retry.

**`TriggerConfig.run_at` must be naive (no tzinfo)**: When you pass a timezone-aware `datetime` (e.g., `datetime(..., tzinfo=timezone.utc)`) as `run_at`, Pydantic raises a `ValidationError` with "naive" in the message. The IANA timezone must be declared separately via the `timezone` field.

**`TriggerConfig.timezone` rejects abbreviations like `"CST"` or `"EST"`**: `zoneinfo.ZoneInfo("CST")` raises `ZoneInfoNotFoundError` because abbreviations are ambiguous. Always use full IANA names like `"America/Chicago"` or `"Asia/Shanghai"`.

## New-joiner traps

- `JobModel.limit` is a field with default `10` that appears to be a pagination hint for the repository. It is stored in the database alongside business data. This field was probably intended for API responses and should not have been on the persistence model.
- `OngoingExecutionResult.should_notify` defaults to `False` for ONGOING jobs. Only the final "completed" execution should notify the user. The LLM is responsible for setting `should_notify=True` only when `should_continue=False`.
- Comparing `job.status == JobStatus.ACTIVE` works because `JobStatus` is `str, Enum`. The string `"active"` and `JobStatus.ACTIVE` are equal.

## 2026-08-19 — TriggerConfig.end_at（recurring 调度地平线）

新增可选字段 `end_at: Optional[datetime]`：recurring（scheduled）job 的
平台级"排到哪天为止"。约定与 `run_at` 完全一致——naive 本地时间 + 由
`timezone` 字段声明时区（同一 validator 拒绝 aware 值；`end_at` 也计入
"time-bearing 字段必须带 timezone"的 model validator）。执行方在
`job_trigger.py`（谓词 `_job_scheduling.past_schedule_horizon`）——下次
fire 落在地平线之后 → COMPLETED 而非重排；one_off 忽略该字段（单次运行
没有"下一次 fire"，_rearm_cooled_jobs 对 one_off 有显式守卫）。语义一句
话：recurring 认、one_off 不认。**写 next_run 的点分两类，别当成"全部一致"
（round-5 review B）：认地平线的 5 个** = SCHEDULED finalize、ONGOING
finalize 的两支（hook 失败的机械回退支 + hook 接管后的 else 支，后者查 hook
重排的 next_run 是否越线，四轮 review 补，因为 hook 正常接管才是常走路径）、
两个重武装侧门（`_rearm_cooled_jobs` / `_heal_unscheduled_active_jobs`）；
**有意不认地平线的 4 个（已知有界缺口）** = `job_recovery.rearm_user_no_quota_jobs`
（登录/额度恢复边缘）/ `resume_job`（用户手动恢复）/ `reschedule_job`（用户
改执行时间）/ `_resume_eligible_no_quota_jobs`（15min backstop）——都是复活
PAUSED 态 job 的恢复路径，最坏多跑一次，随后 finalize 越线完结（finalize 是
权威兜底）；不给它们加守卫是因为完结一个从未在本轮运行的 PAUSED 态 job 与
finalize 的 instance 完结语义不同（PAUSED 态 instance 非 in_progress）。默认
None = 老 job 逐字不变（铁律 #6）。首个消费方是
onboarding 引导 Agent 的每日 check-in；试用期提醒/倒计时/N 天课程是同一
原语的后续候选。**暴露面已同批接线**（否则原语只有 Python 直调可达）：
MCP 工具 schema（`_job_mcp_tools.TriggerConfigArg.end_at: NotRequired[str]`
+ job_create/job_update docstring）、`JOB_MODULE_INSTRUCTIONS`（SCHEDULED
行 + 第 4 节可选字段，教模型"用户给了有界时长就用 end_at，别指望自己记得
暂停"）、agent 侧 job 摘要（`until {end_at}`）、前端 `TriggerConfig` 类型
与 Jobs 详情（`jobs.expanded.endAt`）。JobScheduleEditDialog 有意不加——
`reschedule_job` 的 `_RESCHEDULE_FIELDS`（2026-09-10 前叫 `_TIME_FIELDS`）不含 end_at，传了会被静默忽略。这是调度语义（"日程排到何时"），不是 agent_loop 上限，
不触碰铁律 #14——ONGOING 的 max_iterations 是既有先例。
