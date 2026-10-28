---
code_file: src/narranexus/platform/module_system/plugins_boot.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — `boot_turn_plugins()`：按回合活的进程走只读 boot

本地 NexusPower runner 每回合一个进程、跑完即退，永远到不了 `mark_host_healthy`。它此前用 `boot_executor_plugins()`
（workers 角色、可写 boot）——每回合留下一个 `.booting-workers` 标记，第三次对话 supervisor 读到「3 consecutive boots
of workers never reached health」整机进安全模式（本地插件工场 E2E 实锤；上一轮会话的安全模式也是它）。
`_boot(role, inspect=...)` 透传 `boot(inspect=True)`：同一套贡献、不写标记、不写 registry（与 CLI 三次运行进安全模式
是同一个教训）。长命的 executor_service 仍用可写的 `boot_executor_plugins()`（它会报健康）。

## 2026-09-03（批 2b.5）— mcp / workers 进程的插件启动

只登记声明式贡献（tools / mcp servers / skills / workers），不激活用户插件代码（激活需要 backend 的设置
store、服务定位器、事件总线），不注册插件表（backend 拥有迁移）。每角色一个启动标记；到达即视为健康。
注册表已冻结（同进程二次调用、测试）时返回空报告。

## 2026-09-04 · ingress triggers (batch 3c.3)

`boot_channel_plugins()` — the standalone channels supervisor boots the `workers` role so `CHANNEL_TRIGGER_MAP` (a registry view) is populated and overrides applied before any channel starts.

Batch 6c: the mcp/workers boots pass `resolve_from_env()` so `NARRANEXUS_DIST` shapes every role the same way as the backend.

2026-09-07: the mcp/workers boots resolve the runtime bindings too (no snapshot), so bound consumers work in every role.

## 2026-09-07 — boot_executor_plugins

The per-user executor boots the workers role before serving: it runs turns and needs the same contribution set; before this it never booted and every seam self-registered lazily.


## 2026-09-07 — health is declared by the entrypoint, not by the boot (round-2 🟡-6)

`_boot` ended with `report.mark_healthy()` and the comment "reaching this line is
health". It is not: `mark_healthy` clears the boot-crash marker AND moves the
last-known-good snapshot, so a process that populated its registries and then
died before serving still advanced the rollback target — precisely the state safe
mode exists to escape, and it made the marker unable to detect a crash loop for
the four agent-side roles. `mark_host_healthy(role)` is the new, idempotent
signal, called by each entrypoint once it actually serves: the executor at the
end of its lifespan startup (after the framework warmup), the workers supervisor
once the worker tasks and heartbeat are running, the channels supervisor once the
triggers and `/healthz` are up, the mcp runner once the host server is listening.
The backend already worked this way (`backend/main.py` marks healthy after
`auto_migrate` + `fire_startup`).
