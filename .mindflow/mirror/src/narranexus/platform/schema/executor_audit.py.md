---
code_file: src/narranexus/platform/schema/executor_audit.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — 新增 event_type `cull_skipped_busy` + `cull_disabled`

空闲回收器选中了一个用户，又因为**别的进程里有 run 活着**而退让时写入
（[[executor_reaper.py]]）。**无需迁移**（event_type 是 VARCHAR(32) 字符串
约定）。

怎么读这个指标：每一行 = 一个 2026-08-21 之前的回收器**会当场掐死的在途
run**，是跨进程护栏的 L3 度量。只有**真实 run id** 会落这张表 —— "判不出来"
（recording 开关被拉、DB 拨不通）只打日志不写行，其中 DB 拨不通那种本来也
写不进去（要用的正是刚失败的那个 client）。所以**这里是零不等于护栏没事**；
突然掉到零要去查护栏是不是没跑，而不是当成问题消失了（事故教训 #4/#5）。

`cull_disabled` 是配套的另一半：**整轮**候选的判活全部读不出来（回收器停摆）
时写一行，**按轮不按候选**。刻意与 `cull_skipped_busy` 分开 —— 把"判不出来"
混进那个指标会让它的 `run_id` 列出现 `unknown`，"救下了几个 run"就没法读了。
**能落行的成因不止 kill switch**：`live_run_elsewhere` 的 `except` 罩着
`get_db_client()` 和 `first_live_run_id()` 两段，拿到了 client 只是 `events`
那次读失败（锁等待、权限、表被锁）时，往 `instance_executor_audit` 的 insert
完全可能成功。只有 DB **完全**拨不通那种确实落不进来（要用的正是刚失败的那个
client）。所以成因写进 `detail.recording_disabled`，行本身自证 —— 否则 on-call
看到 `cull_disabled` 会直奔 `NARRANEXUS_RUN_RECORDING_DISABLED`，而真实成因可能
在 DB 侧，白跑一趟。

这个 event 证明停摆、但不穷尽停摆：另一半靠 reaper 的周期警告和
`/api/admin/runtime/status` 里 `executor_reaper` 那一段（`stale` / `task_error`
/ `blind_passes`）。

**写入与警告同一个节拍限频**（`_BLIND_WARN_EVERY`）：每轮一行会让行数变成**停摆
时长**的函数（120s 间隔 ⇒ 720 行/天，kill switch 忘关一个月约 2.2 万行），正是本
系列在 `_CullVeto` 里刻意避开的形状（那里避的是 run 时长）。第一个全瞎轮立刻落
一行（`(blind_passes-1) % N == 0` 在 1 时为真），最有价值的那条不延后。
## 2026-08-10 — 新增 event_type `mcp_auth_denied` + `mcp_auth_tokenless`（MCP caller auth）

`mcp_auth_denied`：验签身份对不属于自己的 agent_id 发起工具调用时写入
（OwnerScopedPolicy，[[identity/mcp_auth]]）。audit 与 enforce 模式**都写**。
`mcp_auth_tokenless`：audit 模式下无 token 的 POST，按 60s 窗口聚合采样写入
（detail 含 per (自述 user_id, method, path, port) 计数——port 标明是哪个
module server(一进程 front 全部端口),自述身份就是接入名单，
无自述归 "anonymous"）——「零行」才能读作「全员持证」，安静的日志读不出这个
结论（incident lesson #4/#5）。两者共同构成切 enforce 的度量。同样无需迁移。

## 2026-07-22 — 新增 event_type `executor_unreachable`

加常量 `EVENT_EXECUTOR_UNREACHABLE = "executor_unreachable"`（并入 Literal union），
与 `oom_killed` 并列为编排层记录+surface 的两个 executor-infra fatal。**无需迁移**
（event_type 是 VARCHAR(32) 字符串约定），`counts_since()` 自动纳入、
/admin/runtime/status 自动出现。写入点见 [[step_3_agent_loop.py]]
`_record_executor_infra_event`。

# executor_audit.py — Pydantic model for instance_executor_audit rows

## 为什么存在

为 `ExecutorAuditRepository` 提供类型化的行模型。调用方可以用 `ExecutorAuditEvent`
做类型注解，而不是裸 dict；也集中定义了全部已知 event_type 字符串常量，避免
散落在各处的魔法字符串。

## 这个文件不做什么

不做验证或枚举强制——`event_type` 字段是 `str`，不是 `Literal` 约束的
`ExecutorEventType`。这是故意的：`Literal` 只在函数签名上用作提示，
让新的 event_type 可以在调用方直接传入而不需要先更新此文件。

## 上下游关系

- **被谁用**：`ExecutorAuditRepository.record()` 不直接构造此模型（直接写 dict），
  但外部调用方如果想用类型化对象可以通过 `ExecutorAuditEvent(**row)` 构建。
  测试文件目前只导入了 `ExecutorAuditRepository`，未直接用 `ExecutorAuditEvent`。
- **依赖谁**：仅依赖 pydantic，无项目内依赖。

## 设计决策

- `event_type` 用 `str` 而非 `Enum`，与 `lark_trigger_audit_repository.py` 保持一致
  的约定：模块级常量而非 Enum，DB 列保持简单 VARCHAR，新 event_type 不需要改这个文件。
- `id` 和 `created_at` 均为 Optional——DB 自动填充，Python 端创建对象时可省略。
- `ExecutorEventType` Literal 类型别名供类型检查器用，不在运行时强制。

## Gotcha

无特别陷阱——字段全部 nullable，可在构造 `ExecutorAuditEvent` 时只传必填字段。
