---
code_file: backend/run_lifecycle.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

首条 "Still waiting" 改为 5 s 后（`_DRAIN_FIRST_REPORT_SECONDS`），之后仍每 30 s：Docker 默认 10 s
宽限期内必须能看到 drain 卡在哪个 run 上。drain 仍无期限、绝不取消 run（铁律 #14）；容器里真正的
杠杆是部署侧的 `stop_grace_period`，不是给 drain 加超时（review I3，部署前置项）。

# Run Task Ownership

Uvicorn waits for ASGI handlers, but agent execution deliberately outlives its
WebSocket or HTTP request. The backend must therefore own those detached tasks
and await their complete lifetime before releasing database and worker resources.
The run-ID lookup alone misses runs still waiting for admission or Step 0.

`RunTasks.start` retains tasks synchronously at creation and observes exceptions.
`close` refuses further starts, drains every retained task without cancelling it,
then invokes dependency cleanup once. Its thirty-second wait interval only logs
progress; it is not an agent deadline. Failed or cancelled tasks do not abandon
their peers. Repeated asyncio cancellation and AnyIO cancel scopes cannot let a
shutdown caller escape before drain and resource cleanup finish.

This protects the backend lifespan's graceful-stop path. It cannot survive
SIGKILL or an external supervisor stopping MCP/database dependencies early.
The native launcher's existing three-second kill fallback and unordered stop-all,
and dev-local.sh's group stop, remain outside this guarantee.
