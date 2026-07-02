---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_client.py
stub: false
last_verified: 2026-06-18
---

## Why it exists

Thin async aiohttp client for the NarraMessenger backend's HTTPS-JSON surface.
The whole integration is bearer-token HTTP, so this client is the only place
that talks to the network. Mirrors `telegram_sdk_client.py` shape.

## Design decisions

- **Bearer in the `Authorization` header, never in the URL** — URLs are safe to
  log (contrast Telegram, which embeds the token in the path).
- **Two timeout tiers**: poll uses a 40s total (server long-polls up to 30s);
  connect/ack/send/status use 20s.
- **`_request` raises `NarramessengerAPIError(code, status)` on non-2xx**, so
  callers branch on `status`. `is_permanent_api_error` returns True for 401/409
  (terminal — re-bind), everything else is transient.
- **`chat_send` returns the `data` envelope** (`{command, status, data:{event_id,...}}`)
  unwrapped to `data`.

## Endpoints wrapped

- `connect()` → `POST /api/agent-gateway/connect`
- `poll(timeout_ms)` → `GET /api/agent-gateway/invocations/poll?timeout=...`
- `ack_update_guide(version)` → `POST /api/agent-gateway/update-guide/ack`
- `chat_send(room_id, text, txn_id, conversation_type?)` → `POST /api/agent-runtime/chat/send`
- `status()` → `GET /api/agent-runtime/status`

## Gotchas

- `trust_env=True` on the aiohttp session so HTTP(S)_PROXY env vars are honoured
  (CN-dev proxy support) — same rationale as telegram_sdk_client.
- The base URL comes from the credential (`backend_base_url`); it is `rstrip`'d
  of a trailing slash so path concatenation stays clean.
