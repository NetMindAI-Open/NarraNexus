---
code_file: backend/plugins_factory/routes.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2f.1）— `GET /proposals`、`POST /proposals/{id}/decide`（`by` 取请求 user_id）

## 2026-09-03（批 2c）— `/api/plugin-factory`

独立前缀，避免与框架安装器 `/api/plugins/{id}/install` 撞路由；不在鉴权豁免名单（401 fail-closed）。
list/index/install/enable/disable/acknowledge-permissions/uninstall/upgrade/update-check/rollback/
safe-mode/leave/bisect/{start,answer,stop}/errors（GET/POST）/assets（带 `X-Content-Integrity` SRI 头）。
`set_service` 让测试注入临时 home 上的 service；`main.py` 把启动报告塞给它。`routes*.json` 快照只多这组路由。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`POST /builtin/{id}/enable|disable` toggles builtin feature plugins (404 unknown, 400 protected, 403 cloud), declared before the `/{plugin_id}/...` routes so `builtin` is never read as a plugin id.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

`POST /builtin/{id}/install-deps`.

2026-09-07: `GET /api/plugin-factory/slots` — the slot catalog of the running process.

## 2026-09-07 — proposals/errors are guarded; slots through the service

GET /proposals and decide use the authenticated caller (401 without one); POST /{id}/errors runs through _run (its audit write fsyncs) and is cloud-guarded INSIDE FactoryService.record_error, not at the route: a guard outside _run raises CloudManaged past _run's handler and answers 500 instead of the 403 every sibling mutation returns; GET /slots goes through FactoryService.slots() like every sibling.
