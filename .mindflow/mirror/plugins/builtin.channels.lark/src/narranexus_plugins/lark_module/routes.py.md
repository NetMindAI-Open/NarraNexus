---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/routes.py
stub: false
last_verified: 2026-09-09
---

## 2026-09-09 — 三条 OAuth 路由挂 `structured_envelope`（B-31，#118；复审 C1/I2）

三条路由（login/complete/status）此前对「预期失败」（ownership 拒绝、未绑定 bot）已经
统一返回 `{"success": False, "error": ...}`，但没有任何一层兜住**意外**异常——
subprocess 调用、CLI JSON 解析里的下一个 bug，都会直接穿透路由，被 Starlette 默认的
`ServerErrorMiddleware` 变成一段纯文本 `"Internal Server Error"` 500。前端从纯文本里读
不出 `error` 字段，用户看到的是一片空白或乱码（#120 那次具体的 NameError 早已在 CLI 层
修掉，但这层什么都没接住，下一个类似 bug 换个位置照样能穿透）。

第一版给三条函数体各手抄了一层裸 `try/except Exception`，复审判 Critical：
`_verify_agent_ownership`（= `_ownership.check_owned`）在 owner 查询失败时**故意**抛 503
让 db 宕机成为可告警的 5xx，裸 except 把它吞成 200——正是 `_ownership.py` 注释写死要防
的事；`AuthError` 也是 HTTPException，401/403 同样被吞。另外 `str(e)` 原样回客户端会泄露
驱动异常里的 RDS 主机名。现在三条路由改挂共享装饰器
`@structured_envelope("lark")`（[[../../../../../../src/narranexus/platform/utils/route_envelope.py]]）：
HTTPException 原样再抛，其余异常落成固定文案 + `trace_id` 的信封。与 generic.py 六个动词
同一份实现，不再两种写法。`tests/lark_module/test_auth_error_handling.py` 钉住三件事：
crash→信封且不含异常文本、503/401 不降级（直调 + 真 TestClient 上线）、两次 crash 两个 id。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-11 — /unbind 返回 do_unbind 的信封 VERBATIM（seam byte-parity）

原来 `/unbind` 把 do_unbind 的结果 reshape 成旧的 `{"success": True}`（成功丢 `data.unbound`）
/ 把 `message`hoist 进 `error`（失败丢 `no_credential` 码 + `message` 键），只为迁就前端。
但 seam 的 DirectStore.unbind("lark") 返回的是 do_unbind 原信封，HttpStore 经本路由取回——
reshape 导致 Direct↔Http 不字节对齐（预审 Important）。改为**原样返回 do_unbind 的 dict**
（同 wechat/slack/discord/telegram 兄弟路由）。LarkConfig.tsx 成功路径只读 `res.success`（不受影响）；
`no_credential` 失败是近乎不可达的双重 unbind 竞态。do_unbind 的 `no_credential` 码/`message`
保留（[[test_lark_unbind]] + lark_unbind 工具契约仍依赖）。

## 2026-08-11 — /bind 补传 owner_email

路由 body 收了 owner_email 却没传给 do_bind(既有 bug)——补上，否则 lark_bind 经 seam→路由时 owner_email 在云端丢失。


> 2026-08-10:`_verify_agent_ownership` 不再是本文件定义——模块级别名指向
> `backend/routes/_ownership.py::check_owned`(canonical;DB 故障走 503 而非 200)。


## 2026-07-29 — auth/complete persists bot_open_id

The route's post-OAuth `/open-apis/bot/v3/info` lookup now stores
`open_id` next to `bot_name` via `update_bot_identity`
([[_lark_credential_manager]]), matching the MCP `lark_setup` and
`lark_bind` paths. Without this, a bot bound through the frontend would
have been the one entry point left without the identity
[[lark_trigger]]'s group @-mention gate matches on, silently degrading it
to name-only matching.
## 2026-07-13 — `/set-active` endpoint (activation)

Added `POST /set-active` (flip `is_active` without a re-bind) → **8 endpoints now** (was 7). Primary use: activating a bundle-imported (inactive) Lark credential so the trigger's watcher claims the app's single WS slot. Delegates to `LarkCredentialManager.set_is_active`.

## Why it exists

REST surface for the dashboard's Lark/Feishu binding flow — what
``frontend/src/components/awareness/LarkConfig.tsx`` calls when the
user pastes ``app_id`` / ``app_secret`` and walks through Bind →
Login → Complete → Test / Unbind. Eight endpoints, all mounted under
``/api/lark``. The Lark surface is larger than Slack's because Lark
adds an OAuth device-code login step that Slack doesn't have.

## Design decisions

- **Eight endpoints, shaped by Lark's two-phase auth.**
  - POST ``/bind`` — persist ``app_id`` / ``app_secret`` / ``brand``.
  - POST ``/auth/login`` — kick off OAuth with ``--no-wait``; returns
    an auth URL + device code for the user to authorize in a browser.
  - POST ``/auth/complete`` — finish the login with the device code
    from the previous ``--no-wait`` call.
  - GET ``/auth/status`` — read login state, syncing it back to the DB.
  - POST ``/test`` — fetch the bot's own info to prove the binding works.
  - POST ``/unbind`` — tear the binding down (see below).
  - GET ``/credential`` — sanitized view (NO ``app_secret``) for the UI.
  The device-flow split (``/auth/login`` then ``/auth/complete``) is the
  structural difference from ``slack.py`` — Slack validates tokens
  synchronously on bind, Lark needs a human browser round-trip in
  between, so the state lives across two calls keyed by the device code.
- **``/unbind`` delegates ALL cleanup to ``_lark_service.do_unbind``.**
  The route used to inline the cleanup (CLI profile removal, workspace
  directory wipe, DB record delete, and the ``bus_channel_members`` /
  ``bus_messages`` / ``bus_channels`` reap for every ``lark_`` channel).
  That logic now lives in ``do_unbind`` so the MCP tool ``lark_unbind``
  (``_lark_mcp_tools.py``, which calls ``do_unbind(mgr, agent_id, db)``)
  does the byte-identical thing. REST and natural-language unbind can no
  longer drift — the bus-channel reap is the part you must not let one
  path forget, which is exactly why it was centralized.
- **``/unbind`` keeps the legacy ``{"success": True}`` envelope.**
  ``do_unbind`` returns a richer result, but the route flattens it back
  to the old shape (and surfaces ``result["message"]`` on failure so the
  "No Lark bot bound to this agent." UX is preserved) specifically so
  ``LarkConfig.tsx`` needs no change. The service got richer; the wire
  contract didn't.
- **POST ``/unbind``, not DELETE.** Some proxies strip DELETE bodies,
  and the body carries ``agent_id``. Same rationale documented in
  ``slack.py:unbind_slack_bot``.
- **``/bind`` shares ``do_bind`` with the MCP path too.** Same
  REST-and-MCP-call-one-helper discipline as ``/unbind`` — the dashboard
  and an agent binding itself see identical envelopes for identical input.
- **Auth status is reconciled to the DB on read.** ``/auth/status`` runs
  the CLI ``auth status``, maps it through ``determine_auth_status``, and
  writes the result back via ``mgr.update_auth_status`` only when it
  changed — then echoes it as ``db_auth_status``. The CLI is the live
  truth; the DB column is a cache the UI can read without shelling out.
- **``/auth/complete`` opportunistically captures the bot name.** On
  success it calls ``GET /open-apis/bot/v3/info --as bot`` and stores
  ``app_name`` / ``name`` via ``mgr.update_bot_name``. Bot identity has
  no "self" user concept, so ``+get-user --as bot`` would fail without a
  ``--user-id`` — the bot-info API is the only handle on the name here.
- **Per-endpoint ownership check via ``_verify_agent_ownership``.**
  Local mode (no ``request.state.user_id``) skips enforcement; cloud
  mode requires the agent's ``created_by`` to match the JWT user_id.
  Same pattern as the rest of ``backend/routes``.
- **Pydantic ``pattern`` constraints at the boundary.** ``agent_id`` /
  ``app_id`` must match ``^[a-zA-Z0-9_\-]+$``; ``device_code`` matches
  ``^[a-zA-Z0-9_\-\.]{1,256}$``. Injection-style payloads are rejected
  before they reach the CLI runner or the DB. ``owner_email`` gets a
  cheap ``"@" in`` sanity check, not full RFC validation.

## Upstream / downstream

- **Upstream**:
  - Frontend ``api.bindLarkBot`` / ``larkAuthLogin`` /
    ``larkAuthComplete`` / ``getLarkAuthStatus`` / ``testLarkConnection``
    / ``unbindLarkBot`` / ``getLarkCredential`` (in
    ``frontend/src/lib/api.ts``).
  - FastAPI app router (registers this under ``/api/lark``).
- **Downstream**:
  - ``LarkCredentialManager`` for credential CRUD + auth-status / bot-name
    updates.
  - ``_lark_service`` helpers ``do_bind`` / ``do_unbind`` /
    ``determine_auth_status`` — the logic shared with the MCP tools.
  - ``LarkCLIClient`` (module-level ``_cli`` singleton) — every
    CLI-backed call goes through ``_run_with_agent_id`` (the V2
    workspace-based runner), so each agent's keychain / ``config.json``
    stays isolated under its own workspace.
  - ``get_db_client`` singleton via ``_get_db``.

## Gotchas

- Errors return ``{"success": false, "error": "..."}`` with HTTP 200,
  same envelope-as-payload convention as the rest of the codebase. The
  frontend branches on ``success`` — switching to non-200 status codes
  would break ``LarkConfig.tsx`` silently.
- Ownership failures are "permission denied" in the response body, not a
  403. Agent-ownership is application logic here, not an HTTP-layer
  concern.
- ``/auth/login`` is fire-and-poll by design: it returns ``--no-wait``,
  so a successful response means "auth URL issued", NOT "logged in".
  Login is only real after ``/auth/complete`` succeeds. Don't treat a
  200 from ``/auth/login`` as a connected state.
- ``do_unbind`` is the single owner of the ``lark_`` bus-channel reap. If
  you add a new side effect to unbind (extra table, extra workspace
  artifact), add it inside ``do_unbind`` — NOT in this route — or the
  MCP ``lark_unbind`` path will silently skip it.
- The CLI profile removal inside ``do_unbind`` is best-effort (the
  workspace may already be gone); don't tighten it into a hard failure
  or a re-unbind after a partial teardown will 500.

## 2026-09-04 · plugin-owned router (batch 3c.5)

`ROUTES` — this router is the `backend.routes` contribution of `builtin.channels.lark` (mounted by `backend/plugins_host`, no longer included by `backend/main.py`); its module-internal imports (credential manager / service) are now intra-plugin, and disabling the channel builtin 404s `/api/lark/*` together with the trigger and MCP tools.

## 2026-09-04 · OAuth flow only (batch 4d.3)

bind / test / unbind / set-active / credential moved to the generic `/api/channels/lark/…` router (bind is `do_bind` behind the descriptor's `bind_fields`, unbind is `do_unbind` through the seam, credential is the store's public view, set-active flips `enabled`). This router keeps the Lark-specific device-code OAuth flow: `auth/login`, `auth/complete` (bot identity capture), `auth/status` (auth_status sync).

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.lark_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
