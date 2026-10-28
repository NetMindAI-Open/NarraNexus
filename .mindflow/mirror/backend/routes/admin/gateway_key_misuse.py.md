---
code_file: backend/routes/admin/gateway_key_misuse.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — secret 改为 `Depends(admin_secret_dependency)`

见 [[_admin_secret]]：依赖项先于 body 校验执行，未认证请求一律 403，不再 422 泄露字段名。
行为对持有 secret 的调用方不变。

# admin/gateway_key_misuse.py — 网关 key 异常使用事件落库内部端点

## 2026-08-19（PR#327 第 3 轮审后）— M2 表名单一真相 / M3 拒绝粗粒度 hit_at

- **M2**：`_MISUSE_TABLE` 不再硬编码字符串，改引用 `GatewayKeyMisuseRepository.TABLE`，
  少一份表名副本。
- **M3 hit_at 偏宽收紧**：`fromisoformat` 会接受纯日期（`"2026-08-19"`）或只到小时的值，
  它们归一成**合法但粗粒度**的 `DATETIME(6)` 字面量（午夜/整点），会把只共享同一天/同一
  小时的不同事件塌成一个 `(key_hash, hit_at)` 幂等锚。解析成功后再校验原串确实带时间部分
  （`_has_time_of_day`：含 `':'`）：不带则**丢弃 hit_at**（与不可解析同路径，事件仍落库、
  走列默认落库时间），只是失去去重。测试 `test_coarse_date_only_hit_at_is_dropped_not_collapsed`
  守：纯日期不被存成午夜字面量。

## 2026-08-19（PR#327 审后）— 列宽单一真相 / hit_at 归一化 / `recorded`→`already`

- **I2 列宽收敛到 schema 单一真相**：截断宽度与 `user_id` 阈值不再在 route 硬编码，改从
  [[schema_registry.py]] 的 `varchar_width("gateway_key_misuse", col)` 取（具名常量
  `USER_ID_MAX_LEN` / `RUN_ID_MAX_LEN` / `KEY_HASH_MAX_LEN` / `CALLER_IP_MAX_LEN` /
  `CALLER_UA_MAX_LEN` / `MODEL_MAX_LEN`），route 与 schema 共享一处真相、避免漂移。
  **`user_id` 阈值随列宽变 64**（原 128）：65~128 字符不再被当权威 id 落库，超宽一律落 NULL
  alert-only（详见下 C1）。
- **I1 hit_at 校验 + 归一化（宁可降级不丢行）**：`hit_at` 原样透传，SQLite(TEXT) 存得下但
  MySQL(DATETIME(6)) 非法字面量 1292→500→**丢行**。改为 route 用 [[timezone.py]] 的
  `to_datetime6_literal`（内部 `coerce_utc`，`Z` 按 UTC）解析：成功→归一成
  `YYYY-MM-DD HH:MM:SS.ffffff`(UTC) 再落库；失败→`logger.error` + **丢掉 hit_at**（走列默认
  =落库时间），事件照落。**写路径与幂等反查用同一归一化结果**，故一个 `Z` 形与其偏移形塌到
  同一行（否则查不到刚撞的行会把幂等成功变 500）。
- **M3 `recorded`→`already`**：`recorded` 恒真是死字段。repository 现返回 `(id, deduped)`，
  route 用 `deduped` 填响应 `already`（fresh insert=False / 幂等命中=True），让 deploy 侧能
  观测重试率。

## 2026-08-19 — C1：攻击者可控字段服务端截断，命中必落库（绝不 422/500 丢行）

请求体的字段值是攻击者可影响的（调用方转发它观测到的 caller_ua 等）。此前 `caller_ua`
带 `max_length=256`，超长会 422——即**丢一行**（漏一次处置）。改为：所有字段**去掉长度
校验**，在 handler 里用 `_clip(v, n)` 按列宽逐字段截断（None 透传、超长切到 n），宽度取自
schema 单一真相（见上 I2）：run_id 128 / key_hash 256 / caller_ip 64 / caller_ua 256 /
model 128。目标：任何事件哪怕字段超长也必须落库。

**user_id 特殊**：它驱动处置，截断可能撞上**另一个真实用户**（错误处置），故**绝不截断**；
超过 `USER_ID_MAX_LEN`(=列宽 64) 视作「反解失败」，落 `user_id=NULL` 的 **alert-only** 行
（`disposition_status` 仍 `pending`）+ log error，事件照落、只是拒绝猜是谁。真库往返由 I5 的
[[test_gateway_key_misuse_repository_mysql]] 兜（SQLite TEXT 无宽度，遮不住 MySQL 的
1406「Data too long」）。

## 2026-08-19 — M3：端点幂等（caller-supplied `hit_at` + 唯一去重）

请求新增可选 `hit_at`（权威事件时间），归一化后透传给 [[gateway_key_misuse_repository]]。它是
幂等锚：表上 `(key_hash, hit_at)` 唯一索引让「写成功但响应超时」的重试落回同一行、回同一
id（`already=True`），不产生重复命中行。省略或不可解析 `hit_at` 时用列默认（落库时间），退化
为无去重（与旧调用方兼容）。deploy 侧若要幂等，需在转发时带上权威 `hit_at`（见存疑点/deploy
同步）。

## 为什么存在

安全监控需要一个把「网关 key 的异常/越权使用事件」**权威落库**的单点。本路由就是这个
单点：

- `POST /api/admin/gateway-key-misuse` —— 记录一次网关 key 异常使用事件，写一行
  `gateway_key_misuse`

它是 `gateway_key_misuse` 表的**唯一写方**。归因 100% 走权威结构化信号，绝不 grep 日志
文本——写入的 `user_id` 是调用方（网关是「这把 key 绑定了哪个身份」的权威）**权威反解**
出来的结果，本端点把它当作已经权威的不透明值原样落库。

## 这个文件不做什么

**不解析任何自由文本。** 端点只记录被交给它的声明字段（见 `GatewayKeyMisuseRequest`）；
pydantic 会丢弃请求里的未知字段，所以攻击者无法夹带一个替代归因（比如在额外字段里写
受害者 id）。`user_id` 是**唯一**归因来源，且完全由调用方决定。

**不做处置。** 每条事件以 `disposition_status='pending'` 落库；推进状态、触发响应梯子是
安全监控侧读方的事，本端点不碰。

## 上下游关系

**被谁用**：
- 内部 server-to-server 路径反解后转发（带 `X-Admin-Secret`）。
- `backend/main.py`：`app.include_router(admin_gateway_key_misuse_router, tags=["AdminGatewayKeyMisuse"])`，
  router prefix `/api/admin`，路由 `/gateway-key-misuse`。
- 路径 `/api/admin/gateway-key-misuse` 在 [[auth]] 的 `AUTH_EXEMPT_PATHS` 里（自凭证、
  机器调用、无用户 JWT，与 suspend 同理）。

**依赖谁**：
- [[gateway_key_misuse_repository]]：`GatewayKeyMisuseRepository(db).record(...)` 落库
  （分层：route 不直接拼 SQL，写走 repository，与 [[suspend.py]] 用 `BanAuditRepository`
  同构）。
- `._admin_secret.require_admin_secret`：**共享**的 admin secret 校验 helper（与
  [[suspend.py]] / [[migration.py]] / [[runtime.py]] 同一份，见 [[_admin_secret.py]]）。
  本模块保留 `from narranexus.platform.settings import settings` 的再导出，只为测试能用
  `mod.settings` 覆盖 secret。
- `narranexus.platform.utils.db.db_factory.get_db_client`：取全局 async DB client。
- [[schema_registry.py]] 的 `varchar_width(...)`：列宽单一真相，导出为具名截断/阈值常量。
- [[timezone.py]] 的 `to_datetime6_literal`：hit_at 归一化到 DATETIME(6) UTC 字面量。

## 设计决策

- **X-Admin-Secret 替代 JWT**：能写「谁使用异常」的输入是高危面，普通 JWT 不作为凭证；
  且调用方是无 JWT 的内部机器。未配置 secret → 503、header 缺失或错误 → 403，语义与
  suspend 完全一致（共享 helper，常量时间比较）。executor/agent 无此凭据 → 无法写
  `gateway_key_misuse`。
- **写走 repository、且失败会 surface**：与 advisory 的 `ban_audit` 不同，
  `gateway_key_misuse` 这一行**就是**载荷（监控据它驱动响应梯子），丢一行等于漏一次
  处置。所以 `GatewayKeyMisuseRepository.record` 不吞异常；写失败让端点回 5xx，调用方
  （及其日志/告警）能看到「权威记录没落库」。
- **`user_id` 可空 = alert-only**：反解失败时调用方传 `user_id=None`，端点照样落一行供
  人工分诊；响应梯子绝不在 NULL id 上触发，故永不伪造可处置归因。None 值在 DB facade
  被过滤、由列默认兜底（`disposition_status='pending'` 与时间戳仍写上）。
- **所有字段无长度校验，改服务端截断**：见顶部 C1 节。攻击者不能靠超长字段把一次真实
  命中打成 422。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：端点 503 → **根因**：共享
  `require_admin_secret` 在 expected 为空时直接 503（防空 secret 匹配空 header 绕过）。
- **触发**：请求夹带任意额外字段 → **症状**：被 pydantic 丢弃，不落库 → **根因**：
  `GatewayKeyMisuseRequest` 只声明白名单字段，归因只认 `user_id`。
- **触发**：DB 写 `gateway_key_misuse` 失败 → **症状**：端点 5xx（与 ban_audit 的静默吞掉
  相反）→ **根因**：这一行是权威处置输入，刻意不做 best-effort。

## 命名 / 中性纪律

对外一律以「网关 key 异常/越权使用事件」的通用语汇描述，不含任何检测策略、特征或识别
逻辑。所有字段都是调用方观测/反解出的值，本层不赋予额外含义。

**本仓是 public 开源仓，本端点不承载检测策略。注释与测试也算对外**：规则标识 / 权重 /
调用方组件名 / 阈值来源一律不写进注释、docstring 或测试字面量——攻击者读本仓不应能推出
任何监控规则。
