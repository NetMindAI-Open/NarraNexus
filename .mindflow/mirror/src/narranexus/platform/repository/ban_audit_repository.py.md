---
code_file: src/narranexus/platform/repository/ban_audit_repository.py
last_verified: 2026-08-19
stub: false
---

# ban_audit_repository.py — 账户状态变更的追加式审计写入

## 为什么存在

账户停用机制（见 [[suspend.py]]）每次切换账户状态都需要留痕：谁（`actor`）在何时、对哪个用户、做了什么动作（suspend / reinstate），以及一段外部提供的不透明说明。本文件就是这条审计的单点写入器——`ban_audit` 表之上一层薄薄的 best-effort writer，按 `user_id` 组织，从 `service_audit` 记录器泛化而来。

暴露 action 常量 `ACTION_SUSPEND = "suspend"` / `ACTION_REINSTATE = "reinstate"` / `ACTION_WARN = "warn"` 供调用方使用，避免字面量散落。

**2026-08-19 起该审计轨迹同时承载 sensitive-operation warning**（`action="warn"`，见 [[warn.py]]）：敏感操作即时警告端点每次实发写一行 `ACTION_WARN` 审计，与 suspend / reinstate 共表——审计层对动作类别保持中性，`reason` 承载调用方传入的不透明 opaque category（永不进用户消息）。

## 这个文件不做什么

**中性、无策略。** `reason` 与 `evidence_ref` 是调用方传入的**不透明字符串**，本层从不解析、分类或校验它们，也不知道账户状态为何改变——它只记录「状态变了」这件事本身（谁、何时、什么动作）。它不含任何检测词汇、特征或枚举。

不做状态变更本身（那是 `UserRepository` 写 `users.status`，才是真相源）。不保证写入成功。不对外抛异常。

## 上下游关系

**被谁用**：
- [[suspend.py]]：`suspend_account` / `reinstate_account` 每次调用后 `await BanAuditRepository(db).record(...)`。
- `narranexus/platform/repository/__init__.py`：re-export `BanAuditRepository` 进包门面。

**依赖谁**：
- 注入的 async DB client（构造函数 `db_client` 参数，**故意不标类型**，与 `ServiceAuditRepository` 一致——标类型只会引入无谓的加载顺序耦合）。
- `narranexus.platform.utils.db.schema_registry` 注册的 `ban_audit` 表（列：id / user_id / action / reason / evidence_ref / actor / created_at；`idx_ban_audit_user_id` 索引）。
- `loguru.logger`：写失败时记 WARNING。

## 设计决策

- **Best-effort 写，永不向调用方抛异常**：`record` 整个包在 try/except 里，写失败只记一行 WARNING（"row dropped; audit is advisory"）。审计行是咨询性的——丢一行绝不能让运维请求的状态变更失败。`users.status` 更新是真相源，本表只是追踪轨迹。
- **`record` 新增 `prev_status` kwarg（2026-08-13）**：记录动作发生前的账户状态（不透明，一个 `users.status` 值），写进新增的 `prev_status` 列（见 [[schema_registry.py]]）。suspend / reinstate 都传它。可选，默认 None。
- **追加式（append-only）**：一次 action 一行，只增不改。`history(user_id, limit=50)` 读回某用户的状态变更，按 `id` 倒序（最新在前），读失败同样吞掉返回空列表。**排序下推 DB（2026-08-13）**：改为 `self._db.get(TABLE, {...}, limit=limit, order_by="id DESC")`，由 DB 侧排序 + 截断，去掉原来的 Python 侧 `rows.sort()+切片`。
- **从 `ServiceAuditRepository` 泛化**：沿用「一张表 + 一个 best-effort 写方法 + 按 user_id 键」的既有形态，不发明新模式。
- **构造函数不继承 `BaseRepository` 的强类型 client**：与 service_audit 同款，注入未标类型的 client，避免 import 期加载顺序耦合。

## Gotcha / 边界情况

- **触发**：DB 写 `ban_audit` 失败（连接断、表缺失等）→ **症状**：审计缺一行，只在日志里留 `BanAudit write failed (...)` WARNING，调用方无感 → **根因**：advisory 写，异常被吞。排查停用是否生效要看 `users.status`，不要以审计行是否存在为准。
- **触发**：`history()` 查询失败 → **症状**：返回空列表而非报错 → **根因**：读同样 best-effort，避免审计查询把上层拖挂。
- **触发**：`reason` / `evidence_ref` 传入超长或含特殊字符 → **症状**：原样落库（列为 MEDIUMTEXT）→ **根因**：本层不校验，把它们当纯不透明字符串。

## 命名 / 中性纪律

表名与列名保持对反滥用中性：这是一条通用的「账户状态审计轨迹」。`reason` / `evidence_ref` 是外部调用方写入的不透明取证引用，本仓库层不赋予任何含义，也不描述其可能的来源或分类。
