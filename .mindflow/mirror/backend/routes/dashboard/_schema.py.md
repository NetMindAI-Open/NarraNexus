---
code_file: backend/routes/dashboard/_schema.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r1 C1）— `paused_spend_cap` 进 `queue_status` Literal 与 `QueueCounts`

同 2026-08-27 那节的教训再演一次：routes.py 一旦发出 `paused_spend_cap`，Literal 不含它就是
整个 `/agents-status` 500。这次补齐的同时把「Literal / QueueCounts 字段 == `_LIVE_JOB_STATES`」
写成测试（`tests/backend/test_dashboard_live_job_states.py`），下一个状态漏在这里会在 CI 红，
不再靠人记 Gotcha。

## 2026-08-27 — 补齐 PendingJob / QueueCounts:这里曾让 `/agents-status` 直接 500

下面 Gotcha 里那条「`JobQueueStatus` 的值必须和 `_LIVE_JOB_STATES` 对齐」被违反了
很久,后果比「字段对不上」严重得多:

[[routes.py]] 早就在发 `cooling` / `paused_no_quota` / `blocked_failed` 三个
queue_status,而这里的 `Literal` 只列了五个。`/agents-status` 带
`response_model=DashboardResponse`,所以**只要有任何一个 job 落到这三个状态,
整个接口就是 500**——不是那一行缺失,是整个看板打不开。同时 routes.py 发的是
`next_run_at` + `next_run_timezone`,这里的字段却还叫 `next_run_time`,于是排期
时间被 Pydantic 静默丢弃,当时的前端消费者读 `j.next_run_at` 永远是 undefined。

现在三处一起补齐:`PendingJob.queue_status` 扩到八个值、字段改名成
`next_run_at`/`next_run_timezone`、`QueueCounts` 加三个计数(原来 routes.py 算了
`total` 却没有对应的分项字段,总数和分项之和对不上)。

**教训**:响应模型和填充它的路由之间没有编译期检查,Literal 收窄了就是运行时
ValidationError。加 job 状态时,`_LIVE_JOB_STATES` / 这里的 Literal /
`QueueCounts` 字段 / 前端 `JobQueueStatus` 四处必须一起动,**没有任何一处有编译期
保险**——原来兜底的那道穷尽类型约束在 `QueueBar.SEGMENT_CLS` 上,该组件已于
2026-08-27 删除。

# backend/routes/dashboard/_schema.py — Intent

## 为什么存在
Pydantic 响应类型的**唯一真相源**（SSOT）for `GET /api/dashboard/agents-status`。

关键职责：**用类型系统把权限边界焊死**——owner-only 字段不能出现在 public 变体上，由 Pydantic `extra='forbid'` + `Literal[True/False]` discriminated union 强制。即使 `to_response` factory 写漏，validation 层拒绝序列化。

## 上下游
- **上游**：`backend/routes/dashboard/_helpers.py::to_response`（factory）、`backend/routes/dashboard/routes.py` 路由响应
- **下游**：前端 `frontend/src/types/api.ts` 里手工复刻了同样的类型（TS 侧，Pydantic → TS 没自动化生成；drift 风险见 Gotcha）
- **平行**：`frontend/src/types/api.ts` 的 `OwnedAgentStatus / PublicAgentStatus` 必须和这里**字段-by-字段**对齐

## 设计决策
1. **Discriminated union via `owned_by_viewer`**：`Literal[True]` 和 `Literal[False]` 才是真 discriminator；普通 `bool` 字段 + 默认值在 Pydantic v2 + FastAPI response_model 里不 work。序列化出错会很隐蔽，所以必须 Literal。
2. **`ConfigDict(extra='forbid')` on PublicAgentStatus**：防御性措施。Factory 里写 `sessions=[]` 传给 public 会在 validation 就 raise，而不是序列化成功然后泄漏。
3. **`running_count_bucket` 替代精确数字** on public 变体（TDR-13）：侧信道防御，防止通过流量分析识别大客户 agent。
4. **v2.1 新增的 owner-only 字段**都在 `OwnedAgentStatus` 内：`verb_line / queue / recent_events / metrics_today / attention_banners / health`。Public 变体**刻意不含**这些。
5. **`action_line: str | None`** 而不是空串——`null` 让前端能明确渲染 `—`。
6. **v2.2 G3 新增 `StaleInstance` + `stale_instances`**：`StaleInstance` 是一个轻量 Pydantic 模型（instance_id / module_class / description）。`OwnedAgentStatus.stale_instances: list[StaleInstance]` 专供 UI 渲染 zombie badge。这个字段**不**触发 `health=error`——stale 是提示性的，不是告警性的。`PublicAgentStatus` **故意不含**此字段。

## Gotcha
- **TS 类型手工复刻**：后端加字段若忘了同步 `frontend/src/types/api.ts`，`tsc` 不报错（TS 对多余字段宽容）。没有自动化契约测试。加字段 checklist：改这里 → 改 types/api.ts → 跑 tsc。
- `running_count_bucket` 的字面值列表（`'0' | '1-2' | '3-5' | '6-10' | '10+'`）和 `dashboard/_helpers.py::bucket_count` 的输出是**隐式耦合**——改其中一个必须改另一个。
- `PendingJob.queue_status` 的 8 个值和 `_dashboard_helpers._LIVE_JOB_STATES`（去掉 `running`）**必须对齐**——改枚举两处都要动。这条曾被违反并导致 `/agents-status` 500，见本文件 2026-08-27 那节。
- 字段添加到 `OwnedAgentStatus` 时若是 owner-only：务必用 `ConfigDict(extra='forbid')` 保护 `PublicAgentStatus`（已做），并在 `tests/backend/test_dashboard_v21.py::test_v21_public_variant_still_locked_down` 的 `forbidden` 集合里加上新字段名，否则白名单漏检。
- `stale_instances` 是 owner-only 字段——`PublicAgentStatus` 不含它（已受 `extra='forbid'` 保护）。如果前端需要展示 zombie badge，必须从 `owned_by_viewer=true` 的响应分支读取。
