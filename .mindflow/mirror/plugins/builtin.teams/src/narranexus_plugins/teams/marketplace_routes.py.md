---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/marketplace_routes.py
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

## 2026-07-22 — 二轮 review 修复:CSRF 守卫走共享 helper

`_require_publisher` local 分支不再从兄弟路由模块 import 私有 `_reject_cross_origin`,
改用 `backend/auth.py` 的共享 `reject_cross_origin`。行为不变,依赖方向理顺
(路由不互相 import 私有符号)。

## 2026-07-22 — review 修复:固定文件名 + CSRF 守卫

publish 固定写 `upload.nxbundle`;`_require_publisher` 在 local 分支复用 skill 的 `_reject_cross_origin`。


# routes/marketplace_teams.py — /api/marketplace/teams/*

The teams half of the /api/marketplace namespace (skills/* is the sibling,
reserved for exactly this at main.py mount time). GET list/detail/download
are public reads (desktop clients fetch anonymously — auth.py's marketplace
public-read prefix list now covers teams/* too). install-preflight resolves
identity + runs the LOCAL importer via the service; confirm reuses the
existing POST /api/bundle/import/confirm (zero new install code). publish/
delete are staff-gated on cloud, open in local mode (loopback + OS-user
boundary), mirroring the skill publish policy. Route order: /download and
/install-preflight declared before /{template_id} (FastAPI matches in order).
Install is fork semantics — no per-user installation audit table; forked
agents/teams ARE the record (team.source = 'bundle:<id>').

Batch 6b.3: moved from `backend/routes/marketplace_teams.py`; exports `ROUTES` (RouterSpec at `/api/marketplace/teams`) so the host mounts it from the manifest instead of `main.py`.
