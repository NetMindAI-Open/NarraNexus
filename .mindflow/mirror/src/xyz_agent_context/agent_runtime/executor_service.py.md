---
code_file: src/xyz_agent_context/agent_runtime/executor_service.py
stub: false
last_verified: 2026-06-17
---

## 2026-06-17 — 日志写到 user 目录

`main()` 启动时把 loguru 文件 sink 落到**该用户 workspace 目录**下的
`.executor_logs/`(`_resolve_executor_log_dir`:容器只挂了一个 user 子目录,
取那个唯一子目录;取不到则回退 base)。这样每个用户的 executor 日志隔离、
随挂载卷持久化到宿主,便于按用户排查。stderr sink 保留(`docker logs` 仍可用)。
文件日志 best-effort(失败只 warning,不挂服务)。

## Why it exists

The agent-loop **Executor** — a thin FastAPI service that is the ONLY
tier which spawns the claude/codex CLI. Given an assembled prompt + the
resolved (scoped) provider configs + the workspace path, it runs the
LOCAL agent-loop driver and streams the raw event dicts back as NDJSON
(`POST /agent-loop`). This is the data-plane half of the
control-plane/data-plane split (binding rule #20).

## Security shape (the point of extracting it)

- **No platform master secrets.** Started WITHOUT the platform `.env`;
  the only credential it sees is the per-run scoped LLM key, arriving in
  the request body and applied to a ContextVar for the loop's duration.
  So `env` inside the agent shows nothing sensitive, and a compromise of
  this container yields ~nothing persistent.
- **No database.** All DB work (pipeline steps 0-2.5) happened in the
  orchestrator; the executor only runs the loop it's handed.
- **No self-recursion.** The executor container does NOT set
  `AGENT_EXECUTOR_URL`, so `get_agent_loop_driver` resolves to the LOCAL
  claude/codex driver here (the remote driver is only used by the
  orchestrator).

## Gotchas / future

- Streaming is NDJSON: `{"event": {...}}` per line, `{"error": {...}}` on
  failure. The remote driver re-raises on the error line to match
  local-driver exception semantics.
- Raw event dicts are JSON-encoded with `default=str` — if an event
  carries a type that doesn't round-trip cleanly, `ResponseProcessor`
  (orchestrator side) could see a degraded value; watch this when
  flipping the remote path on in prod.
- Per-agent/per-user workspace isolation is a DEPLOYMENT concern layered
  on top (per-user container mounting only `workspaces/{user_id}`) — not
  this module's job. This module just runs the loop it is given.
