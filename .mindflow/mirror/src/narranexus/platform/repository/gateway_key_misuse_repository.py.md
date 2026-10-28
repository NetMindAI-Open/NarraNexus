---
code_file: src/narranexus/platform/repository/gateway_key_misuse_repository.py
last_verified: 2026-08-19
stub: false
---

# gateway_key_misuse_repository.py — 网关 key 异常使用事件落库写方

## 为什么存在

安全监控需要把「网关 key 的异常/越权使用事件」结构化落库，供监控侧只读驱动响应。本文件
是 `gateway_key_misuse` 表之上的数据访问写方——一次事件一行，把权威归因与观测字段落库。
分层：端点（[[gateway_key_misuse.py]]）不直接拼 SQL，写走本 repository，与 [[suspend.py]]
经 [[ban_audit_repository]] 落 `ban_audit` 同构。

## 这个文件不做什么

**不反解、不解析。** `user_id` 是调用方（网关是「这把 key 绑定了哪个身份」的权威）**权威
反解**出来的结果，本层当作已经权威的不透明值原样存，从不解析文本，也不知道这个 id 是
怎么得出的。不做处置判断（`disposition_status` 只写初始 `pending`）。

## 上下游关系

**被谁用**：
- [[gateway_key_misuse.py]]：`POST /api/admin/gateway-key-misuse` 里
  `GatewayKeyMisuseRepository(db).record(...)`。
- `narranexus/platform/repository/__init__.py`：re-export `GatewayKeyMisuseRepository` 进包
  门面（与 `BanAuditRepository` 并列）。

**依赖谁**：
- 注入的 async DB client（构造函数 `db_client`，**故意不标类型**，与
  `BanAuditRepository` / `ServiceAuditRepository` 一致，避免加载顺序耦合）。
- `schema_registry` 注册的 `gateway_key_misuse` 表（见 [[schema_registry.py]] 的 08-19 节）。

## 设计决策

- **不吞异常（与 ban_audit 的关键区别）**：`ban_audit` 是 advisory 旁路、丢一行无碍，
  故其 `record` 吞掉写异常。`gateway_key_misuse` 相反——**这一行就是载荷**：监控读它驱动
  响应梯子，丢一行 = 漏一次处置。所以本 `record` 不包 try/except，写失败向上抛，让端点回
  5xx、调用方能感知「权威记录没落库」。
- **返回 `(id, deduped)`（M3，PR#327 审后）**：`id` 便于端点回执与监控 watermark 对齐
  （`id` 兼作 watermark PK）；`deduped` 为 `False`=fresh insert / `True`=幂等命中塌回既有行，
  让端点把死字段 `recorded=True` 换成可观测的 `already`（重试率）。原先只回 `int`。
- **`user_id` 可空 = alert-only**：反解失败时传 None，仍落一行供人工分诊；响应梯子绝不在
  NULL id 上触发。None 值在 DB facade（`AsyncDatabaseClient.insert` 过滤 None）落到列默认，
  故 alert-only 行也会拿到 `disposition_status='pending'` 与时间戳。
- **只写初始状态**：`STATUS_PENDING = "pending"`，推进状态是监控/dispositioner 的事。
- **追加式（append-only）+ 幂等重试（M3）**：一次事件一行。`hit_at` 由**端点**先归一化成
  DATETIME(6) 契约 `YYYY-MM-DD HH:MM:SS.ffffff`(UTC) 再传进来（本层不再解析文本）。当带
  `hit_at` 时，表上 `(key_hash, hit_at)` 唯一索引让「写成功但响应超时」的重试落回同一行：
  `record` 捕获**唯一冲突**（判定走共享 [[dialect_errors.py]] 的 `is_unique_violation`，
  双方言 sqlite `UNIQUE constraint failed` / mysql `Duplicate entry` / `1062`），据
  `(key_hash, hit_at)` 反查既有行、返回 `(其 id, True)`——**幂等成功，不是吞掉失败**。
  反查键用的正是端点写入时的同一归一化 `hit_at`，故必能命中刚撞的那行。其余任何写错误
  照旧上抛（丢一行 = 漏一次处置）。冲突时 key_hash 与 hit_at 必为非空（唯一索引不在 NULL
  key_hash 上触发），故反查可靠。`hit_at` 省略（或端点判为不可解析而丢弃）时用列默认（落库
  时间），无去重。此唯一冲突判定与 [[instance_link_repository]] /
  [[channel_seen_message_repository]] 等六处共用同一份谓词（铁律 #8 收敛，PR#327 I1）。

## Gotcha / 边界情况

- **触发**：所有字段都传 None（含 user_id）→ **症状**：仍成功落一行（`disposition_status`
  非 None）→ **根因**：facade 过滤 None 后 dict 仍非空，不会触发「空 insert」ValueError。
- **触发**：DB 写失败 → **症状**：异常上抛、端点 5xx（**不**静默）→ **根因**：见上「不吞
  异常」。排查事件是否落库直接查 `gateway_key_misuse`，这是唯一真相源（教训 #5：业务事件
  落 DB）。

## 命名 / 中性纪律

表名/列名对安全监控保持中性：这是一条通用的「网关 key 异常使用记录」。字段是调用方观测/
反解出的不透明值，本层不赋予分类含义，也不描述其可能来源。
