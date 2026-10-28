---
code_file: backend/routes/admin/suspend.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — secret 改为 `Depends(admin_secret_dependency)`

见 [[_admin_secret]]：依赖项先于 body 校验执行，未认证请求一律 403，不再 422 泄露字段名。
行为对持有 secret 的调用方不变。

## 2026-09-10（review r3 I1）— suspend 不再压平 BLOCKED / BLOCKED_FAILED / RUNNING

`_pause_jobs_for_suspended_principal` 现在只暂停 `SUSPENDABLE_JOB_STATUSES`（PENDING / ACTIVE / COOLING）
的 job（仓库层白名单）。下文 r2 段「非终态」一词已不准确：等依赖的 BLOCKED / BLOCKED_FAILED、在飞的
RUNNING、自动暂停的 PAUSED_NO_QUOTA / PAUSED_SPEND_CAP 都保持原状，因此 reinstate 恢复成 ACTIVE 的
只会是本来就可调度的 job，依赖链不会被封/解封提前拉起。`jobs_paused` 计数随之只数这三种状态。
锁：`tests/backend/test_admin_suspend_route.py::test_suspend_then_reinstate_keeps_dependency_blocked_jobs_blocked`。

## 2026-09-10（review r2 I-B）— 封号与解封对称：reinstate 恢复 suspend 暂停的那批 job

r1 之后 suspend 会把执行主体名下所有非终态 job 打成 `paused/banned`，而 reinstate 只翻
`users.status`，一条都不恢复——误封后即便 5 分钟内解封，用户的晨报/心跳 job 也全部永久停跑且无任何
信号。**最终语义（两半对称）**：
- **suspend**（仅当本次请求把账户切进停用态，`not already`）：`JobRepository.pause_jobs_for_execution_principal`
  暂停以该用户身份执行的非终态、**且当前不是 `paused`** 的 job，写 `paused_reason="banned"`。已经
  `paused` 的 job（用户自己暂停的 `'user'`、或 reason 为 NULL）**不再被改写 reason**——否则解封时会把
  用户自己的暂停一并恢复。对已停用账户重复 suspend 不补暂停；仍在跑的 job 由 [[job_trigger]] 的逐 job
  门在下次到期时拦下（门写入的 reason 是账户实际状态）。
- **reinstate**（仅 `BANNED → ACTIVE`）：在 `update_user → 审计 → 清缓存` 之后，最后一步 best-effort
  调 `_resume_jobs_for_reinstated_principal` → builtin.job 的 `jobs.resume_for_principal` 服务
  （`job_recovery.resume_jobs_paused_for_principal`）。只选 **`status='paused'` 且 `paused_reason ∈
  NON_TRANSACTING_USER_STATUSES`**（banned/blocked/deleted——suspend 与 poller 门写的全部取值）、执行主体
  谓词与 pause 逐字相同的 job；每条走 job 层恢复而非盲翻状态：`compute_next_run` 从**现在**起算（封禁期间
  错过的触发不补跑），越过 `end_at` 的周期 job 改判 COMPLETED（清 `next_run_time`、实例标 completed），
  其余走 `resume_job`（清暂停/退避状态、ACTIVE）。
- **响应体**：`ReinstateResponse` 新增 `jobs_resumed: int`、`jobs_resume_error: Optional[str]`（additive）。
  恢复失败或 builtin.job 未加载时账户照样恢复、审计照写，错误写进 `jobs_resume_error`，不静默；遗留的
  job 用户仍可在 Jobs 面板逐条恢复。
锁：`test_suspend_then_reinstate_resumes_the_paused_jobs_forward`（往返 + `next_run_time` 向前 + 用户自停
保持）、`test_reinstate_leaves_non_suspension_pauses_alone`、`test_reinstate_completes_a_job_already_past_its_end_at`、
`test_reinstate_survives_a_job_resume_failure_and_still_audits`、`test_reinstate_reports_when_builtin_job_is_not_loaded`。


## 2026-09-10（review r1 I2/I3/I4）— job 暂停改为最后一步、best-effort、按执行主体批量

三条复审意见一次落地：
- **I4 顺序**：原来 `update_user → 暂停 job → 写审计 → 清缓存`，暂停 job 中途抛错 → 请求 500，
  但账户**已经 BANNED**、job 部分暂停、**审计一行没写**、中间件缓存没失效。现在顺序是
  `update_user → 审计 → 清缓存 → 暂停 job`，暂停包在 `_pause_jobs_for_suspended_principal`
  的 try/except 里（沿用 `_invalidate_cache` 的 best-effort 先例）：账户状态是真相源，审计与
  缓存失效不能被 job 表的抖动带走；[[job_trigger]] 的 `_non_transacting_status` 逐 job 门是
  持久兜底。**不静默**：`SuspendResponse` 新增 `jobs_paused: int` 与
  `jobs_pause_error: Optional[str]`（additive，老调用方不受影响），日志也带上。
- **I2 口径**：选行改成「会以该用户身份**执行**的 job」——
  `JobRepository.pause_jobs_for_execution_principal`（[[job_repository]]），与 poller 的
  `exec_uid = related_entity_id or user_id` 完全一致。owner 是被封者但 `related_entity_id`
  是正常用户的 job **不**暂停（poller 会照跑，这里再标 banned 就是两个组件打架）。
- **I3**：一条 UPDATE、返回 rowcount，不再有 500 行截断与 N 次往返。
锁：`test_suspend_pauses_jobs_that_execute_as_the_suspended_user`、
`test_suspend_leaves_jobs_that_execute_as_another_principal`、
`test_suspend_pauses_more_than_five_hundred_jobs`、
`test_suspend_survives_a_job_pause_failure_and_still_audits`。

## 2026-09-09 — B-13：suspend 同请求内暂停该用户的活跃 job

**问题**：`suspend_account` 只翻 `users.status`，从不碰 `instance_jobs`。job 调度层
（[[job_trigger]]）对 `users.status` 全零引用，于是被封号用户的定时 job（如「每日
签到」）继续按计划触发，天天撞 `Key is blocked` 401，直到 job 层自己独立发现账号
被封（B-13 的另一半修复，见 job_trigger 的 `_is_user_banned`）才会在下一次 poll
周期停下——中间那段窗口纯属浪费重试。

**修法**（顺序与选行口径已于 2026-09-10 修订，见上一节）：`suspend_account` 在
`not already` 时调用 `_pause_jobs_for_suspended_principal(db, user_id)`——同一个请求、
同一次调用栈内完成，不等下一次 job poll。跳过终态 job（`completed`/`cancelled`/`failed`，反正不会
再跑）和已经是 `paused_reason="banned"` 的 job（幂等；2026-09-10 r2 I-B 起改为跳过**所有**已 `paused` 的 job，见顶部）。用 `JobStatus.PAUSED` +
`paused_reason="banned"`，不是新状态值——`paused_reason` 是自由字符串字段
（`max_length=32`），加一个新取值是纯 additive 变更，不碰 schema。

**为什么允许 backend 路由直接 import `JobRepository`**：`repository/` 是铁律里
明确的「central」层（不是可热插拔 Module），`backend/routes/` 直接调用 repository
是既有模式（本文件已经在用 `UserRepository`）——不违反铁律 #3（Module 独立）。

**为什么不在这里检查 banned 用户「谁能恢复」**：`reinstate_account` 只管把
`users.status` 翻回 `ACTIVE`，刻意不联动恢复 job——一个被封号又解封的用户，其 job
走普通的手动/自动恢复路径（用户主动 resume，或原本就没被判 no-quota 的 job 保持
`paused` 等用户自己操作），恢复账号本身不该悄悄把所有 job 重新拉活，那是另一个
决策（用户可能就是想封号期间顺便清理掉这些 job）。

# admin/suspend.py — 账户停用（account suspension）HTTP 端点

## 为什么存在

平台需要一个通用、可复用的开关，用来把某个用户账户的状态在「可交易 / 已停用」之间切换，并留下一条中性的操作审计。本路由提供三个自凭证的运维接口：

- `POST /api/admin/suspend` —— 把账户状态置为已停用（写 `users.status = banned`）
- `POST /api/admin/reinstate` —— 把账户状态恢复为 `active`
- `GET  /api/admin/account-state/{user_id}` —— 读取账户当前状态

三者都用 `X-Admin-Secret` header 校验 `settings.admin_secret_key`（与 [[migration.py]] 的 migrate-identity 同一自凭证模式），驱动方是私有运维方而非用户 JWT。

独立成一个路由文件（而非塞进 `admin/quota.py` 或 auth 路由），是因为它改写的是账户主体的**可用性状态**，鉴权模式、调用者、风险等级都与额度管理或普通登录完全不同。铁律 #3（模块独立）。

## 这个文件不做什么

**它不持有任何策略（policy-free）。** 它不判断谁该被停用、也不判断为什么——那属于外部私有调用方。`reason` / `evidence_ref` 是**不透明的自由文本**，本层原样转交给审计层，绝不解析、分类或校验。本文件对「一个账户如何走到停用状态」一无所知，只提供切换开关本身。

不遍历用户批量停用；批量由调用方逐条 POST 驱动。不做任何检测、打分或识别工作——这些概念不存在于本层。不保证 `ban_audit` 审计行一定落库（审计是 best-effort，见下）。

## 上下游关系

**被谁用**：
- 私有运维方 / 内部工具：带 `X-Admin-Secret` 调用三个端点，切换或查询账户状态。
- `backend/main.py`：`app.include_router(admin_suspend_router, tags=["AdminSuspend"])`，router 自带 prefix `/api/admin`。
- 三条路径都在 [[auth]] 的豁免名单里：两个 POST 进 `AUTH_EXEMPT_PATHS`，路径参数形式的 GET 读端点进 `AUTH_EXEMPT_PREFIXES`（`/api/admin/account-state/`）。

**依赖谁**：
- `narranexus.platform.repository.user_repository.UserRepository`：读用户、写 `users.status`。
- `narranexus.platform.repository.job_repository.JobRepository`（2026-09-09 起）：suspend 成功后暂停所有会以该用户身份执行的非终态 job（B-13，`pause_jobs_for_execution_principal`）。
- `narranexus.platform.repository.ban_audit_repository.BanAuditRepository`（+ `ACTION_SUSPEND` / `ACTION_REINSTATE` 常量）：写审计行。
- `narranexus.platform.schema.UserStatus` + `NON_TRANSACTING_USER_STATUSES`：状态枚举，以及三面共享的「不可交易」集合（`_SUSPENDED_STATES` 直接指向它，见下）。
- `._admin_secret.require_admin_secret`：**共享**的 admin secret 校验 helper（与 [[migration.py]] / [[runtime.py]] 同一份，见 [[_admin_secret.py]]）。本模块仍保留 `from narranexus.platform.settings import settings` 的再导出，只是为了让测试可以通过 `mod.settings` 覆盖 secret（helper 读的是同一个 settings 单例对象）。
- `backend.auth.invalidate_account_state`：**惰性 import**，停用/恢复后清掉 middleware 的账户状态缓存，让改动在本进程内立即可见。

## 设计决策

- **X-Admin-Secret 替代 JWT**：能停用任意账户是高危操作，普通 JWT（含 staff 角色）不作为凭证。而且被停用的账户手里也没有可用 JWT，用用户认证路径去 gate 这几个端点本身就是循环依赖。未配置 secret → 503（视为「功能未启用」的误配置，宁可拒绝也不敞开），header 缺失或错误 → 403。校验逻辑是**共享 helper** `require_admin_secret`（[[_admin_secret.py]]），且比较用 `hmac.compare_digest`（常量时间，防止用响应时延爆破 secret）。与 migrate-identity、runtime/status 一致——三处现在是同一份实现，不再各自 copy-paste。
- **`suspend` 幂等**：`_SUSPENDED_STATES` 现在直接指向共享的 `NON_TRANSACTING_USER_STATUSES`（`{BANNED, BLOCKED, DELETED}`），与 auth middleware / WS gate / 登录 gate 用同一个真相源，绝不漂移。若账户已处于任一「不可交易」状态，则不再写 `users.status`，直接返回 `already=True` 的成功。`banned` 是本机制专属的值；`blocked` / `deleted` 是既有的终态，同样算作「已停用」不重复置位。
- **`reinstate` 只复活本机制停用的账户**：`reinstate_account` **仅当** `user.status == BANNED` 时才写回 `ACTIVE`。若账户是 `blocked` / `deleted`（本开关从未设置过的既有终态），则**拒绝**：返回 409（body `{"reinstated": False, "not_suspended_by_this_mechanism": True, "status": <prev>}`），`users.status` 保持不变。否则会把一个跨机制的终态悄悄翻活，属于越权副作用。
- **`prev_status` 审计列**：`ban_audit` 新增 additive 列 `prev_status`（`schema_registry` auto_migrate 增量补列，无破坏性迁移风险，不触铁律 #6）。suspend 记录它替换掉的状态、reinstate（含被拒绝的那次）记录它试图恢复前的状态。审计层的 `record(..., prev_status=...)` 新增该 kwarg。
- **审计每一次调用都写，包括幂等 no-op 与被拒绝的 reinstate**：即使状态没变、或 reinstate 被 409 拒绝，也记一行审计——让「谁在何时请求过」可追溯。审计写入通过 `BanAuditRepository.record`，是 best-effort，永不把异常抛回本路由（丢一行审计不能让运维请求失败）。
- **停用/恢复后清缓存**：`_invalidate_cache` 惰性 import `backend.auth.invalidate_account_state`，best-effort 清掉 middleware 的 30s TTL 账户状态缓存，让本进程内立即生效。惰性 import 是为了让本路由在 import 期不拉入 auth middleware（也方便测试单独跑本路由）；清缓存失败只记 WARNING，不影响主流程（跨进程 staleness 由 TTL 兜底）。被拒绝的 reinstate 不清缓存（状态没变）。
- **`banned` 作为 suspend 写入值**：`suspend_account` 统一写 `UserStatus.BANNED`，与既有的 `blocked` / `deleted` 区分开，使本机制有自己的专属状态值，`reinstate` 只需把行恢复成 `ACTIVE`。
- **`reason` / `evidence_ref` 请求侧上界**：pydantic `Field(default=None, max_length=4096)`，只是挡住失控大 payload 进审计行（后端列仍是 MEDIUMTEXT，上界是请求护栏而非存储上限）。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：三个端点全部 503 → **根因**：共享 `require_admin_secret` 在 expected 为空时直接 503，防止空 secret 匹配空 header 绕过鉴权。部署侧告警 watcher 依赖 503 == 「功能关闭」，503/403 状态码语义**不可改**。
- **触发**：对一个 `blocked` / `deleted` 账户调 `reinstate` → **症状**：返回 409、`not_suspended_by_this_mechanism=True`，`users.status` 不变，但仍落一行 reinstate 审计（带 prev_status）→ **根因**：reinstate 只复活 `BANNED`，绝不悄悄翻活既有终态。
- **触发**：对一个已 `banned`/`blocked`/`deleted` 的账户再次 `suspend` → **症状**：返回 `suspended=True, already=True`，`users.status` 不变，但仍落一行审计 → **根因**：幂等成功语义 + 审计记录每一次调用。
- **触发**：`suspend` 成功但 `ban_audit` 写失败 → **症状**：`users.status` 已改、接口正常返回，但审计缺一行（只在日志里留 WARNING）→ **根因**：`users.status` 是真相源，审计是 advisory 旁路，刻意不因审计失败回滚状态变更。
- **触发**：`invalidate_account_state` 清缓存失败，或停用发生在另一个 backend 进程 → **症状**：被停用账户可能在最多约 30s 内继续用已签发的 JWT 交易 → **根因**：middleware 账户状态缓存的 TTL 上界；这是刻意的「可用性优先」权衡（见 [[auth]]）。

## 命名 / 中性纪律

本模块对外一律以「账户停用 / moderation」的通用语汇描述，不含任何检测策略、特征或识别逻辑。`reason` / `evidence_ref` 是外部调用方设置的不透明字符串，本层不赋予它们任何词汇含义。
