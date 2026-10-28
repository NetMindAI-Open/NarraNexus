---
code_file: backend/routes/admin/_admin_secret.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `admin_secret_dependency`

`Depends` 形式的同一检查。作为依赖项它在请求体校验**之前**解析：没带 secret 的请求得到
403，而不是一个把 body 字段名列出来的 422（审计发现 migrate-identity 等端点泄露 schema）。
五个 admin 路由文件全部改用它；`require_admin_secret` 保留给依赖项内部调用。

# admin/_admin_secret.py — 自凭证 admin 路由的共享 secret 闸门

## 为什么存在

三个自凭证 admin 路由——身份迁移（[[migration.py]]）、运行时状态
（[[runtime.py]]）、账户停用（[[suspend.py]]）——都不由用户 JWT 驱动，而是由私有
运维方或无头 watcher 驱动，鉴权方式完全一致：`X-Admin-Secret` header 对上平台
`settings.admin_secret_key`。这道校验原本在三个文件里各 copy-paste 一份，会漂移。
本模块把它收成**唯一一份**——一个私有 helper 模块，形态对标
[[artifacts/_token.py|_token.py]]。

## 对外接口

`require_admin_secret(provided: str) -> None`：成功返回 None；未配置 secret →
`HTTPException(503)`；header 缺失或不匹配 → `HTTPException(403)`。

## 设计决策 / 契约

- **503/403 状态码语义不可改**：503 == 「没配 secret」——功能虽开但没设 secret 属
  误配置，不是敞门，宁可拒绝。**部署侧告警 watcher 依赖 503 == 「功能关闭」**。
  403 == header 缺失或错误（已认证但不被许可）。改这两个码要同步改 watcher。
- **常量时间比较**：用 `hmac.compare_digest(provided.strip().encode(),
  expected.encode())`，避免用响应时延爆破出 secret。取代旧的 `provided.strip() !=
  expected` 明文比较。
- **读 settings 单例**：helper `from narranexus.platform.settings import settings`。
  各路由模块也各自再导出一份 `settings`（`# noqa: F401`），只为测试能经
  `mod.settings` 覆盖 `admin_secret_key`——因为 settings 是单例，patch 它的属性对
  本 helper 同样可见。

## Gotcha

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：三类 admin 端点全部
  503 → **根因**：expected 为空时直接 503，防止空 secret 匹配空 header 绕过鉴权。
- **私有模块**：`_` 前缀，只给同目录 admin 路由 import，不进 router、不对外暴露。
