---
code_file: backend/plugins_factory/service.py
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — builtin rows honor the ACTIVE distribution's `excludes`

`_builtin_rows` used to compute `enabled` from `registry.json` `builtin_overrides` alone. A builtin the running
distribution excludes (cloud: `builtin.browser`, `builtin.auth.local`, `builtin.nexus_plugins_module`) is never loaded and
its routes are never mounted (`backend/main.py` mounts `/api/browser` only when `builtin.browser` registered), yet it was
reported enabled, so the frontend kept its UI (Browser panel + `BrowserNotices` polling a 404 route -> red banner for every
cloud user). Rows now carry `excluded: true` and `enabled: false` for those ids. The frontend drops the UI of every row that
is not enabled, and `excluded` additionally overrides `protected` there.

The excluded set is the lifespan boot's `BootReport.excluded_builtins`, which `backend/main.py` assigns to the factory
service (`boot_report`) — nothing is re-parsed. When no report exists yet the backend's own resolved distribution
(`backend.plugins_boot.distribution()`, resolved once from `NARRANEXUS_DIST`) answers, so a listing is never more optimistic
than the process. Tests give the service a `BootReport` like they already do for `deps_missing`; there is no test-only seam
(an earlier `resolve_distribution` field was dropped).

`set_builtin_enabled` delegates the rules (unknown id, excluded by the distribution, protected) to the kernel's
`check_builtin_toggle`, the same function the `narranexus` CLI calls, fed with this service's BootReport-derived excluded set —
`_guard_mutation` still runs FIRST, so cloud answers 403 before any 400. `install_builtin_deps` uses `refuse_if_excluded`. A
refusal is a `RegistryError` (HTTP 400): an override or pip install for something the distribution never loads is a silent no-op
the UI would report as done. The disable cascade skips excluded dependants (no dead override, not listed in `also_disabled`);
no shipped distribution can reach that today (their dependency edges are all-in or all-out), a custom one that drops
`builtin.teams` but keeps `builtin.message_bus` does. Not guarded on purpose: `record_error` (an error log for an id the UI never loads is harmless).

## 2026-09-08 — 退出安全模式同时清零三角色启动计数器

`leave_safe_mode` 只翻 registry 标志时，`.booting-<role>` 里残留的计数（如 2）会让下一次启动立刻再次进安全模式，
插件根本没机会加载。「退出」= 用户说「再试一次」，所以三个角色的 `BootMarker` 一并 `exit()`。

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2f.1）— 提案与审计

`proposals()`/`decide_proposal()`：用户在工场页批准/拒绝 Agent 的提案，批准即 `SelfExtensionService.apply_decision`
（enable + scope + 权限确认 / 安装 / 升级）；`record_error` 同时写审计 `ui_error` 行供 observe 窗口读。

## 2026-09-03（批 2d）— 列表行带 `frontend`/`activation_events`/`protected`

前端加载器只靠这一个端点就知道要登记哪些 gate、哪些事件激活、去哪取 bundle。

## 2026-09-03（批 2c）— `FactoryService`

把内核件拼成工场页需要的一个对象：`RegistryStore`（状态）、`Installer`（来源/依赖/落位）、`Index`
（搜索 + 黑名单）、`Bisect`、上次 `BootReport`（谁加载了/谁被隔离）、每插件有界错误环（前端 errorSink 上报）。
云端只读：所有变更先 `_guard_mutation` 抛 `CloudManaged`（路由答 403）。`asset_path` 只在插件
`frontend/dist` 下解析且拒绝穿越。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`list()` also returns `builtins` (id/display_name/enabled/protected/provides/dependencies from the manifests + overrides). `set_builtin_enabled()` writes `builtin_overrides` and cascades a disable to dependants (`because: <id>`); protected builtins refuse; enabling pops the override.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

Builtin rows carry `on_demand` / `pip` / `deps_missing` (from the boot report); `install_builtin_deps` retries the install on the local build (400 for a builtin without on-demand deps, 404 unknown).

## 2026-09-07 — enable respects the permissions gate; acknowledge goes through the installer helper

set_enabled(True) refuses (InstallError → 400) a plugin whose declared permissions are not acknowledged; acknowledge_permissions delegates to installer.acknowledge_permissions so the acknowledged tokens are recorded and the plugin is enabled in one step.

## 2026-09-07 — blocklist always consulted; proposals are per caller; error log bounded; slots()

_installer() always asks the index for the blocklist (it used to depend on whether someone had opened the index first). The unavailable-blocklist decision is fail-CLOSED, and this paragraph used to say the opposite: when `Installer.blocked is None` (offline, no cached copy) every REMOTE source is refused with `InstallError` telling the operator to retry online or install from a local path (`kernel/plugins/install/installer.py`). `LocalSource` is the ONE exemption, and it exists only so an offline desktop can still install from a path it already has — a local directory was never covered by the index's revocation signal anyway. Do not "restore" a fail-open branch here: it is the supply-chain gate on remote installs. install_builtin_deps uses the lazy installer (the raw field was None in production: a guaranteed 500). proposals(user_id=…) returns the caller's own agents' proposals only and decide_proposal answers not-found for another user's (no existence oracle) while the APPLY keeps the proposal's own identity. record_error is a MUTATION and calls `_guard_mutation()` as its first statement like its 13 siblings — the guard belongs here, not on the route, so `routes._run`'s `except CloudManaged` turns it into the same 403 every other mutation returns instead of letting it escape as a 500 (the frontend error sink cannot tell "refused by design" from "backend broken"). The GETTER `errors()` stays unguarded: reading the log is a read. It then accepts only installed plugins and caps the number of plugin logs (the dict keyed by a raw path segment was a leak). slots() is the slot catalog behind GET /slots.
