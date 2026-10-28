---
code_file: plugins/builtin.channels.telegram/src/narranexus_plugins/telegram_module/telegram_sdk_client.py
stub: false
last_verified: 2026-09-10
---

## 2026-09-10（PR #388 round-2 M3）— `_failure(max_len=)` 自己保证"先 redact 再截断"

`_failure(method, *, max_len=None, **fields)`：字符串字段先 `_redact`、再按 `max_len` 切、再压平换行；非 JSON
分支只传原始 body + `max_len=160`。docstring 承诺的顺序现在是函数自身的性质而不是调用方纪律。
测试 `test_failure_envelope_redacts_before_it_truncates`。

## 2026-09-10 — 信封脱敏结构化（PR #388 review I1）

非 JSON 响应分支的 `error_detail` 之前没过 `_redact`——经代理（`trust_env=True`）拿到的 407/502 HTML 页会回显
含 token 的请求 URL，而 `tg_cli` 把信封原样给 agent。现在所有失败信封都由 `_failure(method, **fields)` 构造，
字符串字段一律 `_redact`；非 JSON body **先整体 redact 再截 160 字符**（token 跨截断边界时前半截不会残留）。
测试 `test_non_json_error_page_echoing_the_url_is_redacted_before_truncation`。

## 2026-09-10 — `_redact`：bot token 在来源处抹掉（复审 round-3 I1）

请求 URL（`_API_BASE` / `_FILE_BASE`）路径里就是 bot token，aiohttp 的 `InvalidURL` 之类异常 `str(e)` 会原样引用
URL。`api_call` 的 `ClientError` 分支、兜底分支的日志句、`download_file` 的网络错误分支现在都先过 `_redact`
（token → `<token>`）再进信封 `error_detail` / `TelegramSDKError.description`；`download_file` 用 `from None` 切断
异常链，避免原始 aiohttp 异常经 `__cause__` 被再次渲染。这样基类 `logger.exception` 打的 traceback 尾行、`tg_cli`
回给 agent 的 JSON、`disabled_reason` 全部无 token；基类 `safe_error_text` 只是第二道兜底。测试：
`test_transient_log_output_including_traceback_never_carries_the_bot_token`（走真 SDK + 捕获 loguru sink 全文）、
`test_download_file_network_error_is_redacted_too`。

## 2026-09-09 — `TelegramSDKError` 带 HTTP status + description（B-28）

dev 日志里 115 条裸 `getUpdates failed`（三个 agent）根本分不清是 token 被撤（401）、另一个
poller 抢了 bot（409）还是 Telegram 抽风（5xx）：异常只有 `code`（=description），非 JSON 响应和
传输异常又被压成 `client_error:<ExceptionName>`。现在：`api_call` 失败信封多带 `error_code`
（Telegram 自己的 error_code，等于 HTTP 状态；传输异常无此键）与 `error_detail`（非 JSON body 的
160 字符片段 / 传输异常的 `str(e)`）；`TelegramSDKError(code, message, *, status, description)` + `from_envelope()`，
`str()` 形如 `getUpdates failed (HTTP 409: Conflict: terminated by other getUpdates request…)`。
热路径包装（getMe/sendMessage/getUpdates/getChat/getFile）全走 `from_envelope`。复审 I5 后 `.code`
保持**短而稳定**：JSON 失败 = Telegram description（原样）、非 JSON body = `http_<status>`、传输异常 =
`client_error:<ExceptionName>`（与 `download_file` 一致）；明细（body 片段 / 异常文本）只进信封的
`error_detail` 与异常的 `description` / `str()`。所以 `_friendly_telegram_error(e.code)` 的兜底分支
不会把 HTML 片段渲染进 bind/test 面板，`telegram_module.send` 回给 agent 的 `error` 也还是短码。

## 2026-07-10 — set_message_reaction (backs react_to_user_message)

New `set_message_reaction(chat_id, message_id, emoji)` over the
`setMessageReaction` Bot API. Coerces message_id to int; best-effort (returns
False, never raises). `emoji` must be in Telegram's allowed reaction set
(👍❤️🔥👀🎉💯😱…). Consumer: `_telegram_mcp_tools.react_to_user_message`.

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
