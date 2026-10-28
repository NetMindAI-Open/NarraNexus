---
code_file: backend/routes/marketplace_skills.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — publish token 先于 body 校验

token 检查原来写在 handler 体内，multipart 先被校验：不带 token、不带 `file` 的请求拿到
422 + 上传字段 schema。现在是 `publish_token_dependency`（`Depends`），在 body 校验之前执行，
缺失/错误 token 一律 403，比较用 `hmac.compare_digest`。无 token 配置时的 cloud 403 /
local 放行 + `reject_cross_origin` 语义不变，也一并挪进依赖项。注意 FastAPI 仍会先把
multipart 读进来（表单解析早于依赖求值），挡住的是「校验错误泄露 schema」，不是读取本身。

## 2026-10-28 — install 要求拥有目标 agent

`POST /{skill_id}/install` 的 `agent_id` 来自 body，没有归属检查：会给别人的 agent 写
`skill_installations` 行并重同步它的发现档案。现在 `assert_owned(request, agent_id)`。

## 2026-07-22 — 二轮 review 修复:CSRF 守卫下沉 + 版本详情

CSRF 守卫从本模块私有的 `_reject_cross_origin` 挪到 `backend/auth.py` 的共享
`reject_cross_origin`(teams 路由也用同一个,不再跨兄弟模块 import 私有函数);
顺带堵上 `Origin: null`(沙箱 iframe / data: 表单)被当同源放行的洞——现在
一律按跨站拒。`GET /{skill_id}` 加 `version` 查询参数,详情按指定版本取,
支撑按版本号安装的 hash 校验。

## 2026-07-22 — review 修复:固定文件名 + CSRF 守卫

publish 不再用客户端 `file.filename`(路径穿越 → 任意文件写),固定写 `upload.zip`。新增 `_reject_cross_origin`:local 无 token 时按 Origin 头挡跨站 POST(CSRF)。


## 2026-07-21 — GET /defaults(stage 9)

公开读端点,返回 is_default 技能卡片;注册顺序在 /{skill_id} 之前(FastAPI
按注册序匹配)。desktop 端 agent 创建时匿名拉取。


## 2026-07-21 — local 模式免 token 发布

`MARKETPLACE_PUBLISH_TOKEN` 未配置时:cloud 仍然 403(多租户,发布默认关
闭);local 放行(loopback + OS 用户边界,与本地 API 信任模型一致)——让
dev/桌面主机能运行自己的 registry。配置了 token 则两种模式都严格校验。


# routes/marketplace_skills.py — /api/marketplace/skills/*

Skill Marketplace API. The `/api/marketplace` namespace is split by object:
this router owns `skills/*`; `teams/*` is RESERVED for the Team/Agent bundle
marketplace (unmerged `feat/in-app-marketplace`) — never claim it here.

## Auth model

Like a package registry: read endpoints (search without agent_id, detail,
batch updates via ?skills=, download) are PUBLIC; workspace-touching
endpoints (install, agent-scoped search annotation/updates) resolve identity
via auth_middleware; publish is gated by the `MARKETPLACE_PUBLISH_TOKEN` env
+ `X-Publish-Token` header (403 when unset — publish is closed by default).

## Route-order gotcha

FastAPI matches in registration order: `/search`, `/updates`,
`/{skill_id}/download` are declared BEFORE `/{skill_id}` on purpose.

## Contract details

- install 409 body: `{"code": "SKILL_ALREADY_INSTALLED"}` (spec §6);
  scan-rejected installs → 400 with the human-readable reason (logged
  server-side with agent/user/skill for prod debugging).
- publish 422 body carries `scan_report` (list of issue dicts) so the
  developer sees exactly which rules fired.
- /download responds with `X-Skill-Version` / `X-Package-Hash` headers —
  the desktop RemoteMarketplaceSource reads them; the temp file is cleaned
  by a Starlette BackgroundTask after the response streams.
- /download increments the download counter (desktop installs are counted
  here; cloud installs are counted by LocalMarketplaceSource).
