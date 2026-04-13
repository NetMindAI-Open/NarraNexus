---
code_file: backend/routes/dashboard.py
last_verified: 2026-04-13
stub: false
---

# backend/routes/dashboard.py

## 为什么存在
`GET /api/dashboard/agents-status` 端点。Dashboard v2 前端的唯一后端入口。聚合 events + jobs + module_instances + active_sessions + narratives 一次响应返回；按 viewer 权限裁剪；按 Running/Idle 分组倒序排序。

v1 已推倒重写（见 archive/2026-04-13-dashboard-v1/）。

## 上下游
- 上游：前端 `frontend/src/lib/api.ts::getDashboardStatus()`（3s/30s 自适应 polling）
- 下游：
  - `backend/state/active_sessions.py` snapshot（并发会话）
  - `backend/routes/_dashboard_helpers.py` 查询 + 组装 helper
  - `backend/routes/_dashboard_schema.py` Pydantic 响应类型
  - `backend/routes/_rate_limiter.py` 2 req/s per-viewer 限流
  - `backend/auth.py::get_local_user_id` / `request.state.user_id`（JWT）

## 设计决策
- **Pydantic discriminated union**（TDR-5）：`PublicAgentStatus` (owned_by_viewer=Literal[False] + extra='forbid') vs `OwnedAgentStatus` (Literal[True])。类型层阻止 owner-only 字段泄漏到 public 响应
- **`?user_id=` 被拒**（TDR-12）：viewer_id 永远从 auth 上下文识别，query param 是身份冒充向量
- **asyncio.gather(return_exceptions=False)**（TDR-8）：任一聚合 query 失败 → 整个 request 500，不做 partial degradation
- **rate limit 2 req/s**（TDR-6）：合法 dashboard polling 是 0.33 req/s，2 req/s 给手动刷新留余量但挡 100-tab DoS
- **排序分组**（TDR-11）：Running 组在前（按 started_at desc），Idle 组在后（按 MAX(events.created_at) desc）。`_earliest_started_at` 对并发 session 取 min

## Gotcha
- action_line 数据源：`events.embedding_text` 对 running 态几乎必 null（Step 4 才写），因此 `build_run_state_for_agent` 从 `instance_jobs.description` / `bus_messages.content` / session channel 取；fallback "Running (kind)"（TDR-4 已知局限 + R11）
- `HTTPException` 429 必须带 `headers={"Retry-After": "1"}`，否则 FastAPI 丢掉 response.headers 设置
- running_count_bucket 是 public 视角的隐私措施，不是纯展示：精确 int 暴露可被用作流量推断攻击（security M-1）
