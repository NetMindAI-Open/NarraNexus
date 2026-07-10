---
code_file: src/xyz_agent_context/module/wechat_module/wechat_sdk_client.py
stub: false
last_verified: 2026-07-03
---

## 2026-07-03 (final) — root cause was the missing `client_id`; emoji strip reverted

The silent-drop incident's REAL rule, proven by controlled probes across two
QR sessions: `client_id` is the server-side per-message dedup key. Our send
payload omitted it, so every send shared one empty key — the FIRST message of
a login session delivered and every later one was silently swallowed as a
duplicate (HTTP 200 + empty body `{}`; there is no `ret` field and no
delivery receipt in this API at all). Fix: payload now carries
`from_user_id: ""`, a per-message `uuid4` `client_id` (stable across the
in-place retry — a retry IS the same message; distinct per chunk), and
`base_info.channel_version` (the constant existed but was only wired into
getupdates).

The interim "non-BMP emoji kills delivery" theory was a coincidental
correlation (the first two lost replies happened to carry 🍉) — with
client_id present, astral-plane emoji deliver and render fine (probe P11).
`sanitize_bmp` and the prompt's emoji ban are reverted. Per-chunk
send-failure logging (lesson #3) stays.

Debugging landmark: rebinding "fixed" delivery only because a fresh login
session has an empty dedup slot — one more delivery, then mute again. If
"only the first message arrives" ever reappears, check payload shape against
the protocol docs before suspecting the platform.

## Why it exists

Low-level HTTP wrapper for the iLink ("ClawBot") gateway — a
**personal-WeChat** bridge hosted at ``https://ilinkai.weixin.qq.com``.
The trigger, the ``wechat_send`` MCP tool, and the bind route all talk
to the same gateway; this module is the single source of truth for the
gateway's quirks so those three callers don't each re-discover them.

iLink is **NOT** the official WeChat Work / OA bot API and **NOT** a
webhook integration. It is a **PULL-only long-poll** gateway: the
trigger holds a ``getupdates`` request open (the server hangs ~35s),
turns each inbound text into a message, and replies via ``sendmessage``.
Because it speaks a personal account, there is no app secret / verify
token / public callback URL — bind is instead a **QR-scan** flow
(``get_bot_qrcode`` → poll ``get_qrcode_status`` until the owner scans →
gateway returns a ``bot_token`` + optional per-account ``baseurl``).

Deliberately has **no NarraNexus dependencies** (httpx only) so it stays
unit-testable in isolation and so the gateway protocol can be exercised
without standing up a DB / module.

## Design decisions

- **Per-request ``X-WECHAT-UIN``, regenerated every call.** The auth
  triple is ``AuthorizationType: ilink_bot_token`` + ``X-WECHAT-UIN:
  base64(str(random uint32))`` + ``Authorization: Bearer <bot_token>``.
  The UIN is **not** a stable identity — it is freshly randomised in
  ``ilink_headers`` on every request, matching the reference
  implementation's observed behaviour. Do **not** "optimise" it to a
  cached value; the gateway expects it to vary.
- **The QR-fetch call is unauthenticated.** ``fetch_qrcode`` and
  ``poll_qrcode_status`` call ``ilink_headers()`` with no token — there
  is no token yet. Everything after bind carries the Bearer token via
  ``ilink_headers(self._bot_token)``.
- **``ret != 0`` on an HTTP 200 is an app-level failure.** This is the
  **load-bearing gotcha** of the whole gateway. ``raise_for_status()``
  only catches transport-level errors; the gateway routinely returns
  HTTP 200 with ``ret != 0`` in the JSON body to mean "session expired /
  bad token / stale context". Both call sites raise ``WeChatSDKError``
  (a ``RuntimeError`` subclass carrying ``ret`` + ``source``) — **not** a
  bare ``RuntimeError`` — so the trigger can branch on the kind of
  failure without string-parsing. ``get_updates`` raises
  ``WeChatSDKError(source="updates")``: the trigger classifies that as a
  **permanent auth failure** so the base class disables the credential
  rather than reconnecting against a dead session every 120s forever (the
  zombie-reconnect incident class — CLAUDE.md lesson #1). ``send_message``
  raises ``WeChatSDKError(source="send")`` internally and treats it as a
  failed send (per-message, never disables the account).
- **A failed chunk aborts the rest of the send.** ``send_message`` posts
  each chunk in order; if a chunk fails both attempts it sets ``ok=False``
  **and breaks** — it does NOT keep posting later chunks. Continuing past
  a gap would deliver a truncated / out-of-order reply under an
  already-False result. The boolean still reports overall failure.
- **Parse JSON regardless of Content-Type.** The gateway returns
  ``Content-Type: application/octet-stream`` even though the body is
  JSON. ``resp.json()`` parses it anyway (httpx doesn't gate on the
  header), so callers must not branch on content-type — always decode as
  JSON.
- **``poll_qrcode_status`` ReadTimeout means "still waiting", not an
  error.** The status endpoint is itself a long-poll: it holds the
  connection until the scan state changes or the timeout elapses. A
  ``ReadTimeout`` / ``TimeoutException`` is swallowed and returned as
  ``{status: "wait"}`` so the bind route re-invokes cleanly rather than
  surfacing a spurious failure to the user mid-scan.
- **``POLL_READ_TIMEOUT = 50s`` > server hang ~35s.** The runtime
  client's default read timeout must exceed the server-side long-poll
  hang, otherwise httpx aborts ``get_updates`` mid-flight. Same shape as
  Telegram's "client timeout > poll timeout" rule. ``send_message``
  overrides the per-request timeout down to 20s because a send shouldn't
  inherit the long-poll-sized window.
- **Reply chunking at ``MSG_CHUNK = 2000``.** WeChat rejects very long
  single messages, so ``send_message`` splits the text and posts each
  chunk as its own ``sendmessage``. ``send_message`` returns True only
  if **every** chunk delivered.
- **One retry per chunk.** By the time the agent replies, the inbound
  was already consumed (the cursor advanced in ``get_updates``). A
  transient send failure would otherwise read as "message read, no
  reply" to the user. So each chunk gets one retry with a 1s pause
  before giving up.
- **``send_text_once`` for the MCP path.** The ``wechat_send`` tool has
  no long-lived client to reuse, so this helper spins up a
  ``WeChatSDKClient``, sends, and closes it in a ``finally``. The trigger
  path instead reuses a per-account client across the long-poll loop.
- **``ILINK_HOST`` / ``CHANNEL_VERSION`` are env-overridable, and a bind
  may return a per-account ``baseurl`` that overrides the host.** Keeps
  the default host out of hard-coding and lets the gateway shard
  accounts onto different base URLs.

## Upstream / downstream

- **Imports**: ``httpx`` only — no NarraNexus packages.
- **Called by (bind flow)**: the WeChat bind route — ``fetch_qrcode`` →
  ``poll_qrcode_status``.
- **Called by (runtime)**: ``WeChatTrigger`` long-poll loop
  (``WeChatSDKClient.get_updates`` / ``send_message``).
- **Called by (MCP send tool)**: ``wechat_send`` → ``send_text_once``.
- **Helpers reused**: ``extract_text`` (concatenate ``text_item.text``
  across an inbound message's ``item_list``) is used by the trigger's
  parse step.

## Gotchas

- **Never trust HTTP 200 alone.** If you add a new gateway call, you
  MUST check ``ret`` in the body before treating the response as
  success — the gateway hides app-level failures behind 200s.
- **Don't branch on Content-Type.** The body is JSON under an
  ``application/octet-stream`` header; any future caller that inspects
  the content-type before parsing will wrongly reject valid responses.
- **Don't shrink ``POLL_READ_TIMEOUT`` below the server hang.** Drop it
  under ~35s and every ``get_updates`` will abort mid-long-poll, making
  the session look dead when it's healthy.
- **The UIN regeneration is intentional.** It looks like a candidate for
  caching; it isn't. Leave ``random.randint`` in ``ilink_headers``.
- ``send_message`` swallows the underlying exception on its final
  failed attempt and just returns False — the **reason** for the send
  failure (e.g. which ``ret``) is not surfaced to the caller. The
  ``WeChatSDKError`` it raises internally carries ``ret``, but it's
  caught and collapsed to the boolean; if send diagnostics get
  important, thread the ``ret`` out rather than relying on the boolean.
- **Only ``get_updates`` errors mean "disable the account".** A
  ``WeChatSDKError(source="send")`` must never reach
  ``is_permanent_auth_failure`` as a disable signal — it's per-message.
  Keep the ``source`` tag correct on any new gateway call you add.
