---
code_file: src/xyz_agent_context/agent_runtime/executor_protocol.py
stub: false
last_verified: 2026-07-15
---

## 2026-07-15 — 协议字段 `mcp_server_urls` → `mcp_servers`（spec 对象）

`build_agent_loop_request` 的 body 字段改为 `mcp_servers:
{name: {"url": str, "headers": {str:str}?}}`，用户 MCP 的鉴权头由此跨
orchestrator→executor 边界。**部署注意**：旧 executor 容器不认新字段（该次
run 的 MCP 集合为空），上线需 backend 与 executor 镜像同批重建并回收存量
nx-exec-* 容器。

## Why it exists

Wire format for the agent-loop Executor boundary. When step-3 (the only
claude/codex spawn site) is extracted into a separate Executor service,
the call that used to be in-process must cross the network. The hard part
is that the **scoped provider credentials normally travel via ContextVar**
(`api_config._claude_ctx/_codex_ctx`, set by the resolver in the
orchestrator) — a ContextVar does NOT survive a network hop. This module
serializes those configs so they cross explicitly.

## Key points

- `serialize_provider_configs()` — orchestrator side; snapshots the
  current task's resolved configs (via `api_config.snapshot_user_config`)
  to plain dicts. `None` entries preserved (reproduce exact ContextVar
  state, e.g. anthropic_helper unset).
- `apply_provider_configs()` — executor side; rebuilds the frozen
  dataclasses and calls `api_config.set_user_config`, so the SDK's
  `to_cli_env` resolves the same scoped key — **without the executor ever
  touching the DB or the resolver** (that's the whole point: executor
  holds no DB creds).
- `build_agent_loop_request()` — the `POST /agent-loop` body. Deliberately
  does NOT serialize `cancellation` (orchestrator cancels by aborting the
  HTTP stream; executor sees client disconnect).
- Lives in the core package (not `backend/`) so both the executor service
  entrypoint and the remote driver import it without a backend dependency.

## Gotcha

Provider config dataclasses are frozen — reconstructed via
`Cls(**dict)`. If a config gains a field, asdict↔kwargs round-trips
automatically; if it gains a non-trivial type, add explicit handling.

## 2026-07-07 — 快照/回放 cli_helper

`_CONFIG_TYPES` 加 `cli_helper: CliHelperConfig`，`apply_provider_configs` 回放时 `set_user_config` 传 `cli_helper`——远程 executor 才能复现订阅 helper 的 ContextVar 状态。
