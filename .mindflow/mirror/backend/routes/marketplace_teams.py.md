---
code_file: backend/routes/marketplace_teams.py
last_verified: 2026-07-22
stub: false
---

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
