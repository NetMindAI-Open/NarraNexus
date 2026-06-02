---
code_file: src/xyz_agent_context/module/telegram_module/telegram_sdk_client.py
stub: false
last_verified: 2026-05-20
---

## Why it exists

Async Telegram Bot API client wrapping aiohttp directly — no
``python-telegram-bot``, no ``aiogram``. Both alternatives carry their
own event loop assumptions and ~20 transitive deps; the Bot API is
plain HTTPS-JSON, so the wrapper is small and we avoid framework
collisions with our own loop.

Mirrors ``slack_sdk_client.py`` shape but stays minimal — the heavy
lifting Slack SDK does (event handler registration, retry policies)
isn't needed here because we long-poll explicitly and surface errors
to the trigger / credential manager directly.

## Design decisions

- **Raw aiohttp, no third-party Telegram SDK.** Bot API is ~80
  methods of POST-JSON. A custom wrapper is ~200 lines vs. a SDK
  bringing webhook servers, FSM, dispatcher abstractions we'd never
  use. Keeps cold start fast and the dependency surface tight.
- **Two API styles intentionally.** ``api_call(method, args)`` is the
  generic dispatcher backing the ``tg_cli`` MCP tool — returns
  Telegram's native ``{"ok": bool, "result"?, "description"?}``
  envelope WITHOUT raising, so agents can branch from the dict
  directly. Hot-path wrappers (``get_me``, ``send_message``,
  ``get_updates``, ``delete_webhook``, ``get_chat``,
  ``get_chat_member``) raise ``TelegramSDKError`` on failure for
  Python-caller ergonomics.
- **Client total timeout 35s.** Long-poll uses ``timeout=30`` server-
  side. Anything ≤ 30s and aiohttp aborts mid-poll. The 5s margin
  covers TLS handshake + request body upload variance.
- **One client per credential / call site, ``close()`` after use.**
  No global session pool — credentials are token-scoped and short-
  lived call sites (bind, status check) shouldn't keep sessions open.
  ``TelegramTrigger`` keeps its long-poll client alive across the
  whole loop and closes it in ``stop()``.
- **``trust_env=True`` on the ClientSession.** aiohttp's default is to
  IGNORE ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY`` env vars
  unless ``trust_env=True`` is set on the session — a long-standing
  gotcha. Enabling it makes proxy support transparent for CN
  developers (Clash / V2Ray on ``127.0.0.1:7897`` etc.) without any
  code change: just ``export HTTPS_PROXY=http://127.0.0.1:7897`` in
  the shell that launches ``run.sh``. ``api.telegram.org`` is blocked
  in mainland China; without this flag every CN dev got a 70+ second
  ``TimeoutError`` on bind even with a working local proxy.
- **``api_call`` swallows network errors as
  ``{"ok": false, "error": ...}``.** The MCP tool is the primary
  caller; agents read the envelope per skill docs and don't expect
  exceptions. Unexpected exceptions are logged via
  ``logger.exception`` and still returned as a failure envelope.
- **``deleteWebhook`` is its own wrapper.** Defensive call before
  long-poll: Telegram refuses ``getUpdates`` with 409 Conflict if a
  webhook was previously set. Idempotent — safe to call repeatedly.
- **``get_chat`` accepts ``@username`` strings.** Used at bind time
  to resolve the owner trust signal — Telegram's ``getChat``
  unifies "lookup by id" and "lookup by handle" through the same
  ``chat_id`` arg.
- **``parse_mode`` defaults to None in ``send_message``.**
  MarkdownV2 escape rules are aggressive (``_*[]()~>#+-=|{}.!\``);
  Phase 4 stays plain-text to avoid a class of 400 Bad Request
  errors. Caller can opt in.
- **Phase 1a — ``download_file`` is a two-step call.** Step 1:
  ``getFile(file_id)`` returns ``{file_path, file_size}``. Step 2:
  HTTP GET against ``https://api.telegram.org/file/bot{TOKEN}/{file_path}``
  returns raw bytes. Token is embedded in the file-host URL path; no
  ``Authorization`` header. We deliberately do NOT log this URL
  (would leak the token). The platform's 20 MiB bot download cap
  lives as ``TELEGRAM_BOT_DOWNLOAD_CAP_BYTES``; ``size_hint`` (from
  the Update's ``file_size`` field) gates a pre-check so oversized
  refs fail fast without hitting the API at all.

## Upstream / downstream

- **Used by**: ``_telegram_credential_manager.bind / list_active``,
  ``telegram_trigger.connect``, ``telegram_module.send_to_agent``,
  ``_telegram_mcp_tools.tg_cli``, ``_telegram_service.do_test_connection``.
- **Wraps**: aiohttp ``ClientSession`` POSTing to
  ``https://api.telegram.org/bot{token}/{method}``.

## Gotchas

- ``api_call`` and the hot-path wrappers have OPPOSITE error
  semantics (envelope vs. exception). Don't unify them — the MCP
  surface and the Python callers want different shapes.
- ``send_message`` casts ``message_thread_id`` / ``reply_to_message_id``
  to ``int``. Telegram requires integer ids; passing strings produces
  400 Bad Request.
- Lowering ``timeout_seconds`` below 31 will make every long-poll
  call fail with ``ClientError`` after 30s mid-flight — silent until
  the trigger backs off.
