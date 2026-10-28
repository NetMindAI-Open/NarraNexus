---
code_file: plugins/builtin.auth.netmind/src/narranexus_plugins/auth_netmind/provider.py
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

# builtin.auth.netmind — provider.py

`NetMindAuthProvider.authenticate(request)` decodes the `Authorization: Bearer` JWT with the backend's `decode_token`; no bearer → `None`; expired/invalid → `AuthError(TOKEN_EXPIRED | TOKEN_INVALID)` (same codes the middleware emitted, so the SPA keeps its session semantics). Returns `{user_id, role, provider, token}`. `CONTRIBUTION` fills `kernel.auth`.
