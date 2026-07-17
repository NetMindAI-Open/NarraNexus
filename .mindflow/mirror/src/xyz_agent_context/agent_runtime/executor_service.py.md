---
code_file: src/xyz_agent_context/agent_runtime/executor_service.py
stub: false
last_verified: 2026-07-15
---

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

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

## 2026-07-13 — office live-preview watch endpoints

新增两个端点支持 office artifact 的实时预览(watch 必须跑在 executor 容器内,因为工作区 + agent 的 officecli 编辑都在这里):
- `POST /watch/ensure` {agent_id,user_id,file} → 在容器内 `ensure_watch`(detached spawn officecli watch),**返回容器为该文件分配到的端口** `{ok,port}`。容器自己拥有端口分配(每文件一个专属端口),后端不再猜端口(改自 2026-07-13:原来是后端 hash 出 port 传进来,多文档并发会串台)。由后端 `/office-watch/open` 云端分支调用,拿到 port 后铸 token。
- `GET /watch/{port}/{path}` → 反代到容器内 `127.0.0.1:{port}` 的 watch 服务(SSE 流式,X-Accel-Buffering: no)。由后端公共代理转发到这里。两者都无鉴权(内网信任,同 /agent-loop),但仍做端口 allowlist 防御纵深。
- `GET /watch/version?agent_id&user_id&file` → 容器内 stat office 文件返回 `{mtime,size}`,给前端 mtime 兜底轮询用(云端工作区在容器里,后端 stat 不到)。由后端 `/office-watch/version` 云端分支调用。
