---
code_file: backend/routes/wechat.py
stub: false
last_verified: 2026-06-25
---

## Why it exists

REST surface for the dashboard's WeChat bind UX
(``frontend/src/components/awareness/WeChatConfig.tsx``). The personal-
WeChat counterpart of ``backend/routes/telegram.py`` — same agent-
ownership-checked, ``{"success": bool}``-enveloped shape — but because
personal WeChat authenticates by **scanning a login QR** (not a token
paste), bind is a two-step QR flow instead of a single ``POST /bind``.

## Design decisions

- **Four endpoints, QR-shaped.**
  - ``POST /qrcode/start`` — fetch a login QR from the gateway.
    Returns ``{qrcode, qr_url}``: ``qr_url`` is the scannable WeChat
    URL the frontend renders; ``qrcode`` is the opaque handle the
    frontend passes back to ``/poll``.
  - ``POST /qrcode/poll`` — the gateway long-polls scan status. On
    ``status:"confirmed"`` it reads the iLink ``bot_token`` +
    ``baseurl`` from the gateway response and persists the binding via
    ``WeChatCredentialManager.bind``. On ``"wait"`` it returns so the
    frontend re-polls.
  - ``GET /credential`` — sanitised view (``mgr.get_public`` — NO
    token).
  - ``POST /unbind`` — remove the binding.
  No "list accounts" / "rotate token" — same YAGNI line the Telegram
  route holds.
- **``/qrcode/poll`` is where bind actually happens — not a separate
  ``/bind``.** The token is never seen by the user, so there's no
  point exposing a paste endpoint. The gateway hands the token to the
  backend at confirm time; the backend persists it without ever
  returning it to the frontend.
- **``bot_token`` is never serialised to HTTP.** ``GET /credential``
  returns ``get_public()`` only. Same rule as Telegram's
  ``to_public_dict()`` — Pydantic doesn't enforce it; the manager
  method does. Returning the raw credential dataclass via FastAPI
  auto-serialisation would leak the token.
- **Agent-ownership check on every endpoint
  (``_verify_agent_ownership``).** Matches ``request.state.user_id``
  (set by upstream auth middleware) against ``agents.created_by`` —
  identical to the Telegram route. In **local mode** there is no auth
  middleware, so ``request.state.user_id`` is unset and the check
  returns ``None`` (every route effectively unauthenticated) — the
  deliberate same local-mode pattern as ``telegram.py``.
- **agent_id pattern ``^[a-zA-Z0-9_\-]+$``** — defense-in-depth at the
  edge against SQL/path injection. Belt-and-braces over the
  parameterised ORM. ``qrcode`` is bounded (≤4096) and ``base_url``
  bounded (≤256) for the same reason.
- **Per-account ``base_url`` comes ONLY from the gateway, never the
  client (SSRF guard).** ``QrPollRequest`` has **no** ``base_url`` field.
  ``/qrcode/start`` never hands a base URL to the frontend, so a client
  could only ever *inject* one — and the backend fetches it server-side,
  which would be a server-side request forgery vector (internal hosts /
  cloud metadata). The poll uses the fixed iLink default host; a genuine
  per-account host is read from the gateway's own confirm response
  (``status["baseurl"]``) and persisted by ``bind``. Don't reintroduce a
  caller-supplied host.
- **``{"success": bool, ...}`` envelopes, not HTTP error codes.**
  Consistent with the rest of the REST surface (``api.ts`` expects
  this). Auth/gateway failures still come back ``200 OK`` with
  ``success=false`` + an explanatory string.
- **Logs explicit at bind / unbind, silent on poll / get.** Poll and
  credential-read are high-frequency (the poll is re-invoked every
  long-poll cycle); only state-changing actions earn audit-log noise.
  The token is never logged.
- **Lazy import of ``get_db_client``.** Avoids module-level import
  cycles between FastAPI boot and the DB backend factory.

## Upstream / downstream

- **Mounted at**: ``/api/wechat/*`` from ``backend/main.py``.
- **Calls**: ``WeChatCredentialManager`` (``bind`` / ``get_public`` /
  ``unbind``), ``wechat_sdk_client.fetch_qrcode`` /
  ``poll_qrcode_status``.
- **Consumed by**: ``frontend/src/components/awareness/WeChatConfig.tsx``
  via ``frontend/src/lib/api.ts`` (``startWeChatQrcode``,
  ``pollWeChatQrcode``, ``getWeChatCredential``, ``unbindWeChat``).

## Gotchas

- ``GET /credential`` returning a raw token would silently break the
  security model — no test pins this beyond ``get_public()``'s
  implementation. Manual eyeball on any edit.
- ``request.state.user_id`` depends on the auth middleware being
  installed in front of this router. Without it (local mode)
  ``_verify_agent_ownership`` returns ``None`` and the endpoint runs
  unauthenticated — intentional locally, but the check belongs at the
  app level; this file trusts it.
- ``/qrcode/poll`` blocks for the gateway's long-poll duration. A
  ``"wait"`` return means the long-poll expired with no scan, not an
  error — the frontend re-polls. Don't add a client-side delay; the
  server call is the pacing.
- Bind logs ``agent=...`` but never the token. Don't add the token to
  log lines for "debugging" — it's a personal-account secret.
