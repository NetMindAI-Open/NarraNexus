---
code_file: packages/narranexus-sdk/src/narranexus/sdk/testing.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2e）— `PluginTestHost`：围绕一个插件目录起最小宿主

上下文管理器：临时插件 home（`NARRANEXUS_PLUGIN_HOME` 进出恢复）→ link 安装 → 新 `Registries` 上跑
内核 `boot(role)` → 暴露注册表、`tables`、`hooks`、`activate()`（内存设置 store + EventBus + 服务定位器）、
`test_app()`（插件路由挂到无鉴权中间件的 FastAPI，测试直接打路由）。退出时 deactivate、卸合成包、关总线。
`plugin_id` 属性在插件被隔离时直接断言失败并给出原因——模板测试全部靠它。

## 2026-09-04 · owner-scoped queries (batch 3c.2)

`names()` and `test_app()` are scoped to the plugin under test: builtin feature plugins (`builtin.teams` in `backend.routes`) boot alongside it, and a plugin author's tests must not break when the host gains a builtin. `all_names()` keeps the unfiltered view for host-composition tests.

`_unshadow_platform_packages` runs on enter: if the plugin dir sits on sys.path (a plugin author ran pytest from inside it) it is removed and a shadowing `backend` import is forgotten, so the builtins that import `backend.*` boot in the plugin's own test run.

Batch 6d: `PluginTestHost.module(name="")` returns the plugin's imported backend package (or a submodule) so plugin tests never import the kernel importer.

## 2026-09-07 — the test host acknowledges the plugin's permissions

PluginTestHost installs with permissions_acknowledged=True — its author is the one running it — otherwise every plugin that declares a permission would boot disabled inside its own test host.

## 2026-09-07 — host version from metadata; env restored on a failing setup

PluginTestHost.host_version defaults to compat.host_version() instead of a hardcoded string; __enter__ restores NARRANEXUS_PLUGIN_HOME (via _restore_env, shared with __exit__) when setup raises.
