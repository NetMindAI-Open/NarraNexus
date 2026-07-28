---
code_file: backend/routes/quota.py
stub: false
last_verified: 2026-07-28
---

## 2026-07-28 — 语义换成钱包，路径保持不变

`GET /api/quota/me` 的**路径故意不改**（前端改动面因此被限制在四个文件），
但返回的东西从「token 计数器」换成了「美元钱包余额」，数据来自 deploy 侧的
quota-api。

三种响应形状必须保持互相可区分：`{enabled:false}`（本地/关闭）、
`uninitialized`（还没开钱包，新用户会短暂处于此态，因为开通是登录路径上的
fire-and-forget）、以及带余额的 `active`/`exhausted`。把前两者合并会让刚注册
的用户看到一个「已用完」的余额。

服务不可达时返回 503 而不是 0 余额 —— 谎报余额为零等于告诉用户钱没了。

## 2026-07-23 — GET /me 响应新增 `free_tier` 锁定块

`get_my_quota` 的 enabled 分支（uninitialized + 完整两种）都带上
`free_tier: {active, model}`。动机：全局 Model Defaults（[[ModelDefaultsSettings]]）
是用户级面板、没有 agentId，拿不到 [[agents_llm_config]] 的 per-agent free_tier；而它
在免费额度期改默认模型同样被运行时静默忽略（[[resolver]] SYSTEM_OK 分支忽略
user_slots）。于是复用本就承载配额状态的 quota/me 暴露同一锁定信号，前端据此渲染诚实
banner。`free_tier` 由单点 `free_tier_lock_for`（[[resolver]]）算出——本路由
只把 `app.state` 的 system_provider / quota_service + db 传进去（review 抓出第一版在此
和 agents_llm_config 各复制了一份 helper，本次收敛到 provider_resolver）。本地模式
`_is_cloud_mode()` 假 → 早返回 `{enabled: false}`，不带 free_tier（前端视作 inactive）。
测试见 test_quota_route.py。

## 2026-07-18 — PATCH /me/preference 删除

免费额度优先成为平台行为（Owner 决策），用户偏好端点整体移除；
`PreferenceRequest`、`QuotaPreferenceLocked` 映射、toggle 后的 job rearm
一并删除。`prefer_system_override` 列保留为耗尽通知的去重闩锁（见
[[resolver]] / [[quota_service]]）。下文 "PATCH /me/preference
(#48)" 一节自此为历史记录。本路由现在只剩 GET /me。

# Intent

Read-only quota view for the signed-in user. Discriminated response shape
lets the frontend avoid duplicating "is the feature on" logic:

- `{enabled: false}` — local mode OR SystemProviderService disabled
- `{enabled: true, status: "uninitialized"}` — cloud user without a quota
  row yet (pre-feature registration; staff can run the migration script
  or call /api/admin/quota/init)
- `{enabled: true, status: "active"|"exhausted"|"disabled", …}` — full
  breakdown, matching the `Quota` schema's public fields.

## PATCH /me/preference (#48)

Maps `QuotaPreferenceLocked` (raised by `quota_service.set_preference` when
re-enabling free tier while budget is zero) to HTTP 409. This is the
defensive backstop; the frontend already disables the toggle in the exhausted
state, so normal users never hit 409.

## Upstream
- Frontend `QuotaPanel` component (polls on page load; hides when
  `enabled == false`)
- Frontend RegisterPage (confirms seed success via round-trip after
  register response signals `has_system_quota: true`)

## Downstream
- `app.state.system_provider.is_enabled()` — primary gate
- `app.state.quota_service.get()` — one read, no write

## Gotchas
- Returns `{enabled: false}` when the services are not yet wired
  (`app.state.system_provider` missing). This is only possible during
  test harness setup before `lifespan` runs; production always wires.
- Does NOT call `require_auth` explicitly — relies on `auth_middleware`
  already having populated `request.state.user_id`. In cloud mode the
  middleware enforces JWT before this route runs; in local mode
  `is_cloud_mode()` returns False and we short-circuit before touching
  user_id at all.
