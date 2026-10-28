---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/routes_dashboard.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

# dashboard/jobs.py — builtin.job's dashboard controls

## Intent

`POST /api/dashboard/jobs/{id}/pause|resume` and `PUT .../schedule` delegate to `job_module.job_recovery` (the portable state-machine core), so they belong to builtin.job: split out of `dashboard/routes.py` in batch 3c.5 and provided through `backend.routes` (`ROUTES`, prefix `/api/dashboard`). Auth/ownership reuse the dashboard's `_resolve_viewer` / `_assert_agent_visible`; behaviour and paths are byte-identical (route snapshot unchanged). Disabling builtin.job removes these controls while the dashboard's reads and the SQL-only retry stay.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.job_module.routes_dashboard:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
