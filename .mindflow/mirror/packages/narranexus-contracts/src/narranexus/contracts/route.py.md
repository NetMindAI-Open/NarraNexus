---
code_file: packages/narranexus-contracts/src/narranexus/contracts/route.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `backend.routes` 位的契约：路由即数据

`RouterSpec(router, prefix, auth, quota_bypass, tags)`。`router` 类型标 `Any`：契约包不 import FastAPI（保持叶子）。
前缀必须以 `/api/` 开头且不以 `/` 结尾；第三方插件由宿主强制落在 `/api/x/<id>`（`plugin_route_prefix`），
防止遮蔽壳路由。`auth` 默认 `"user"`（全局 auth 中间件已 fail-closed），`"none"` 是显式豁免，宿主把前缀
登记进豁免集合——插件不能靠「忘了写」得到公开端点。
