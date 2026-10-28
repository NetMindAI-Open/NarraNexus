---
code_file: backend/plugins_boot.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2b.5）— backend 进程的插件启动装配

把内核 `boot()` 接到本进程的东西上：`KERNEL_REGISTRIES`、`schema_registry.register_table`、`RegistryStore`
（每次现取，尊重被重定向的插件 home）、进程级 `HOST_BUS`/`HOST_SERVICES`、以及 `Activator`（上下文工厂：
`DbSettingsStore` + 该插件的 `backend.settings` schema + 同步 DB 客户端；crash sink 写 `record_crash`）。
`main.py` 在 `auto_migrate` 之前调 `boot_backend_plugins()`（插件表要先注册），之后 `fire_startup()` 激活
`onStartup` 插件并 `mark_healthy()`。注册表已冻结（测试里 lifespan 跑多次）时返回空报告不重复 boot。

## 2026-09-04 · services + host hooks (batch 3c.6)

`HOST_SERVICES` is `KERNEL_REGISTRIES.services` (no separate locator), so builtin services and user-plugin services share one namespace.

Batch 6c: `distribution()` resolves `NARRANEXUS_DIST` once per process (None = all builtins) and `boot_backend_plugins()` passes it to `boot`; `write_runtime_bindings(res)` resolves the slot bindings (default < distribution < `<plugin home>/narranexus.toml` < `NX_BIND__*` env) and snapshots them to `<plugin home>/run/bindings.resolved.json` — a `BindingConflict` is loud, an unbound slot only logged.

Batch 6 fix: `set_host_db(db)` / `host_db()` — plugin contexts get the lifespan's async client (a sync client cannot be built inside the loop); the settings store is built without a loop-bound client (`DbSettingsStore()` drives its own loop).

2026-09-07: `write_runtime_bindings` delegates to `platform.bindings_runtime.resolve_runtime_bindings` (installs the bindings on the registries as well as snapshotting).

## 2026-09-07 — settings fallback covers the DB read; the first boot report survives a repeated lifespan

_settings_for constructs PluginSettings INSIDE the try: the store's DB round-trip happens in the constructor (rows load eagerly), so a transient DB error was escaping as a plugin crash and two restarts auto-disabled a healthy plugin; now it falls back to MemorySettingsStore with a warning. When the registries are already frozen (a second lifespan in-process) boot_backend_plugins returns the first boot's report instead of an empty one that made the factory page list every plugin as unloaded.

## 2026-09-07 — boot refuses index-withdrawn versions

boot() receives blocked_versions from the index CACHE (no network at boot): a plugin the index withdrew after it was installed is rejected at boot as the loader's 'blocked:' state — the README's 'refused at install and at boot' now holds on both halves.
