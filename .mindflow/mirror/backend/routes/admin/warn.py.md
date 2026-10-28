---
code_file: backend/routes/admin/warn.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — secret 改为 `Depends(admin_secret_dependency)`

见 [[_admin_secret]]：依赖项先于 body 校验执行，未认证请求一律 403，不再 422 泄露字段名。
行为对持有 secret 的调用方不变。

# admin/warn.py — 敏感操作即时警告端点

## 2026-08-19（PR#327 第 3 轮审后）— M1：actor 上界从 schema 派生

`WarnRequest.actor` 的 `max_length` 不再硬编码 128，改为 `_ACTOR_MAX_LEN =
varchar_width("ban_audit", "actor")`（schema 单一真相，与 misuse 端点列宽纪律一致）。
`category` 的 4096 **保留字面量**并注明：它是**防失控 payload** 的护栏，非列宽——
`ban_audit.reason` 是 MEDIUMTEXT，`varchar_width` 对它会抛。

## 2026-08-19（PR#327 第 3 轮审后）— C1：注释/mirror/测试去检测策略（public 仓）

本仓是 public 开源仓。此前 warn.py 注释、本 mirror、测试字面量里残留了调用方一侧的
检测策略痕迹（规则标识、权重、调用方组件名、去重窗口来源组件名），攻击者读本仓即可
反推监控规则。逐处中性化，**端点本身保留**（它是 `user_notifications`/`ban_audit` 的写方）：

- 注释/docstring/mirror 里调用方一侧的实现细节（规则标识、权重、组件名、去重窗口来源
  等**类别**）一律删除或改为中性措辞（「私有调用方」「跨仓约定」）——**只记被移除信息
  的类别、绝不引用其内容**，因为删除记录本身也会被反推。其中 `user_id` 一段里调用方侧
  的实现细节删掉，改为「调用方保证它是权威值，本端点不重复校验」。
- 测试字面量改中性占位（opaque_category / internal_monitor）；「opaque category 绝不进
  用户通知」的 `not in` 信任边界断言保留、且与请求体用**同一变量**（断言语义/强度不变）。

## 2026-08-19（PR#327 审后）— actor 上界 + `_as_utc` 抽成共享 helper + 注释去中文

- **M2（本轮）**：`WarnRequest.actor` 原无上界，而 `ban_audit.actor` 是 `VARCHAR(128)`；
  超长 actor 会 1406 让**审计静默丢**。改为 `Field(default=None, max_length=128)` 对齐列宽。
  审计是旁路面包屑（非承重处置行），故这里 422 可接受（与 misuse 端点「必落库、绝不 422」
  的取舍不同）。
- **`_as_utc` → 共享 `coerce_utc`**：原模块内 `_as_utc` 与 misuse 端点归一化 `hit_at` 是同
  一套惯例，抽进 [[timezone.py]] 的 `coerce_utc`（str/datetime → aware UTC，naive 当 UTC，
  不可解析回 None），warn 与 misuse 端点共用一份。行为不变（`.rstrip("Z")` → `.replace(
  "Z","+00:00")` 对尾随 Z 等价）。
- **I3**：`SENSITIVE_OP_WARNING` 上方注释里的中文「铁律」改英文（铁律 #1：代码内无非英文串）。

## 2026-08-19 — M1/M2：dedup 读收窄 + 幂等措辞如实

- **M1**：幂等 dedup 读只需最新一行的时间戳，改为 `db.get(..., limit=1,
  order_by="created_at DESC", fields=["created_at"])`——不再全行拉回一个话痨用户所有
  `abuse_warning` 历史（`payload` 是 MEDIUMTEXT，全行扫描把整条通知体拖回来纯属浪费）。
- **M2**：dedup 是 read-then-write、**无唯一约束兜底**，并非原子。原 docstring 声称
  「a network retry cannot double-notify」是假的（两个真正并发的 POST 会双双通过检查、双双
  插入）。改为如实措辞：它只收敛**顺序**重试；并发下最坏多发一条泛化警告通知——无害（不同于
  重复的处置行），故不为它付一个唯一索引的代价。

## 为什么存在

在硬处置之前需要一层**渐进响应前置**：当私有调用方判定某个软（未坐实）
信号值得给一次提醒时，向该用户投递**一条**固定泛化的系统警告，作为温和的前置干预。
本路由就是这层投递的落点：

- `POST /api/admin/warn-user` —— 写一行 `user_notifications`（`kind="abuse_warning"`）
  + 一行 `ban_audit`（`action="warn"`）审计

## 这个文件不做什么

**不接受、也不回显任何规则细节。** user-facing 文案是模块内的**固定英文常量**
`SENSITIVE_OP_WARNING`，无论收到什么输入都写这一句——检测规则 / 阈值 / 证据绝不
经用户消息泄漏（信任边界硬约束：措辞泛化不暴露规则）。调用方传入的 `category` 是
**opaque**，只进 `ban_audit.reason`，**永不**进用户通知。精细的「哪条规则、什么证据」
留在私有调用方一侧，绝不进用户消息（本仓不承载检测策略）。

**不解析文本、不做归因。** 端点只收 `user_id`（调用方保证它是权威值），本端点不重复
校验、不从日志/容器名取归因，只按传入 `user_id` 查用户。

**不做处置、不减分。** warn 只是投递一条通知 + 留痕，不改 `users.status`、不动风险分
阶梯。

## 上下游关系

**被谁用**：
- deploy 侧私有调用方转发（带 `X-Admin-Secret`，只传 `user_id` + 固定粗粒度 opaque
  `category` + `actor`）。
- `backend/main.py`：`app.include_router(admin_warn_router, tags=["AdminWarn"])`，
  router 自带 prefix `/api/admin`，最终挂载 `POST /api/admin/warn-user`。
- 路径 `/api/admin/warn-user` 在 [[auth]] 的 `AUTH_EXEMPT_PATHS` 里（自凭证、机器
  调用、无用户 JWT，与 [[suspend.py]] / [[gateway_key_misuse.py]] 同理；否则 middleware 先 401、
  端点不可达）。

**依赖谁**：
- `._admin_secret.require_admin_secret`：**共享**的 admin secret 校验 helper（与
  [[suspend.py]] / [[gateway_key_misuse.py]] / [[migration.py]] / [[runtime.py]] 同一把
  `admin_secret_key`，常量时间比较，见 [[_admin_secret.py]]）。本模块保留
  `from narranexus.platform.settings import settings` 再导出，只为测试能用 `mod.settings`
  覆盖 secret。
- [[user_repository]]：`UserRepository(db).get_user(user_id)` 查用户存在性 + 取
  `prev_status`（未知用户 → 404）。
- [[ban_audit_repository]]：`BanAuditRepository(db).record(user_id, ACTION_WARN, ...)`
  写审计（best-effort，与 suspend/reinstate 共表）。
- `user_notifications` 表（见 [[schema_registry.py]]，self-heal 首创的投递面）：直接
  `db.insert` 一行 `kind="abuse_warning"`、`severity="warning"`、`payload` 为固定
  `{"code","message"}` JSON。
- `narranexus.platform.utils.db.db_factory.get_db_client`：取全局 async DB client。
- `narranexus.platform.utils.timezone.utc_now` / `coerce_utc`：dedup 窗口计算基准 +
  `created_at` 归一化（`coerce_utc` 与 misuse 端点共用，见 [[timezone.py]]）。

## 设计决策

- **X-Admin-Secret 替代 JWT**：能给用户推系统警告的输入是高危面，普通 JWT 不作为凭证；
  且调用方是无 JWT 的内部机器（deploy 侧私有调用方）。未配置 secret → 503、header 缺失或错误 →
  403，语义与 suspend/gateway_key_misuse 完全一致（共享 helper）。executor/agent 无此凭据。
- **文案固定泛化 + `code` 供前端本地化**：代码禁中文串（铁律 #1），故 `message` 为固定
  英文常量、另给 `code="sensitive_operation_warning"` 让前端 bell 本地化。这也是信任
  边界要求——端点无论收到什么 category 都写同一句。
- **端点自带幂等（`_DEDUP_WINDOW_SEC=6h`）**：投递前查该 user 最近一条 `abuse_warning`，
  若 `created_at` 在窗内则短路返回 `already=True` 且不重复写行/审计。这一层独立于
  调用方一侧的 durable 台账，专防网络重试导致的重复投递（两层去重）。窗口值与调用方侧
  去重窗口需保持一致（跨仓约定）。
- **`created_at` 归一化 `coerce_utc`（共享）**：DB facade 有的后端回 `datetime`、有的回
  SQLite 的 ISO 字符串；[[timezone.py]] 的 `coerce_utc` 两者都收，naive 当 UTC，无法解析
  的行**忽略**（fail-safe：最坏多发一条，绝不 crash 或误判为「已发」而漏发）。同一 helper
  被 misuse 端点复用于 `hit_at` 归一化，避免两处各写一份漂移。
- **写 notification 直接 `db.insert`、写审计走 repository**：通知行是普通投递（与
  self-heal 同模式，非权威处置输入），审计走既有 `BanAuditRepository`。分层上 route 不
  拼 SQL 的承重写（审计）走 repository，通知复用现成表的既定写入形态。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：端点 503 → **根因**：共享
  `require_admin_secret` 在 expected 为空时直接 503（防空 secret 匹配空 header 绕过）。
- **触发**：忘了把 `/api/admin/warn-user` 加进 `AUTH_EXEMPT_PATHS` → **症状**：
  middleware 先 401、端点根本不可达（gateway_key_misuse 端点批次的教训）→ **根因**：默认路径走用户
  JWT 鉴权，自凭证端点必须显式豁免。
- **触发**：窗内二次 warn → **症状**：`already=True`、不重复写通知/审计 → **根因**：
  端点侧幂等短路。
- **触发**：请求夹带额外字段 → **症状**：被 pydantic 丢弃 → **根因**：`WarnRequest` 只
  声明 `user_id` / `category`（≤4096）/ `actor`（≤128，对齐 `ban_audit.actor` 列宽）。
- **触发**：`actor` 超 128 字符 → **症状**：422（而非静默丢审计）→ **根因**：`actor` 有
  `max_length=128`；审计是旁路面包屑，422 可接受。

## 命名 / 中性纪律

对外一律以「敏感操作即时警告」的通用泛化语汇描述，不含检测策略、特征、规则 id 或识别
逻辑。`category` 是调用方传入的不透明取证类别，本层不赋予含义、只落审计。

**本仓是 public 开源仓，本端点不承载检测策略。注释与测试也算对外**：规则标识 / 权重 /
调用方组件名 / 阈值来源一律不写进注释、docstring 或测试字面量——攻击者读本仓不应能推出
任何监控规则。
