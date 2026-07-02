---
code_file: src/xyz_agent_context/module/wechat_module/wechat_trigger.py
stub: false
last_verified: 2026-06-25
---

## Why it exists

WeChat (iLink) long-poll trigger built on the shared
``ChannelTriggerBase``. Each active ``WeChatCredential`` gets one
``getupdates`` loop; the base class owns dedup, debounce, the worker
pool, the audit log, and inbox writing. This subclass fills the six
abstract hooks (``connect`` / ``parse_event`` / ``is_echo`` /
``resolve_sender_name`` / ``create_context_builder`` /
``load_active_credentials``) plus a ``_process_message`` override (for
the first-DM owner claim) and an ``extract_output`` override (for the
inbox record).

iLink ("ClawBot") is a **personal-WeChat gateway over HTTP** (host
``https://ilinkai.weixin.qq.com``). It is **PULL-only** — a long-poll
``getupdates`` cursor, NOT a webhook. Architecturally this is the
Telegram trigger's twin (no public IP, no webhook), so it deliberately
mirrors ``telegram_trigger.py``.

## Design decisions

- **Cursor, not numeric offset.** Telegram's ``getUpdates`` advances a
  numeric ``update_id`` offset; iLink returns ``get_updates_buf`` — an
  opaque cursor string. ``connect`` reads it out of each batch and
  stores it in ``self._cursors[key]`` (per-agent). There is no integer
  arithmetic; the cursor is whatever iLink last handed back.
- **``connect`` raises on app-level failure (``ret != 0``).** A response
  can be HTTP 200 but carry ``ret != 0`` in its JSON body = an
  application-level failure (expired session / bad token). The SDK's
  ``get_updates`` raises ``WeChatSDKError(source="updates")`` on that
  condition; ``connect`` lets it propagate (its ``finally`` only closes
  the client, it doesn't swallow) so the base class runs recovery. There
  is intentionally **no manual retry here** — the base owns recovery,
  same as Telegram.
- **``is_permanent_auth_failure`` stops the zombie-reconnect.** Overrides
  the base hook to return True for ``WeChatSDKError(source="updates")`` —
  a dead/expired iLink session that reconnecting can never recover. On
  True the base calls ``disable_credential`` (flips the row's
  ``enabled=0`` via ``set_enabled``) and the loop exits cleanly. Without
  this override the base default (False) would reconnect every 120s
  forever against the dead session — exactly the lark-trigger zombie
  incident (CLAUDE.md lesson #1). ``disable_credential`` and
  ``set_enabled`` always existed but were unreachable until this hook was
  wired. Transient network errors are NOT ``WeChatSDKError`` so they keep
  retrying; a ``source="send"`` error never reaches the connect loop.
- **Idle wake-up sleep 0.5s.** When a batch comes back empty, sleep
  ``POLL_IDLE_SLEEP_SECONDS`` so ``self.running`` is re-checked
  promptly and ``stop()`` returns quickly when the account is quiet.
- **``parse_event`` keys on ``context_token``.** iLink exposes no
  per-message id, so ``message_id`` is the ``context_token`` (falling
  back to ``wx_<from_user_id>``). ``chat_id`` = ``sender_id`` =
  ``from_user_id`` (1:1 DM). The full ``raw`` dict is kept on
  ``ParsedMessage.raw`` so the reply path (``wechat_send``) can read
  ``to_user_id`` + ``context_token`` back out to address the peer.
- **v1 is text-only, DM-only.** ``parse_event`` returns ``None`` (skip)
  when there's no extractable text or no ``from_user_id``. ``chat_type``
  is always ``ChatType.PRIVATE`` — personal accounts are 1:1, there is
  no group path. Non-text payloads are out of scope for v1.
- **``extract_output`` scrapes the ``wechat_send`` tool-call ``text``
  arg, NOT ``result.output_text``.** This is the **load-bearing
  regression prevention**, inherited from Telegram's mirror warning.
  ``output_text`` contains the agent's reasoning ("My thought process:
  ...") and would leak chain-of-thought into the inbox. The correct
  source is the ``text`` argument of the ``wechat_send`` tool call.
  ``_extract_wechat_reply`` only matches items whose ``tool_name``
  contains ``wechat_send``; other tool calls in the turn are not
  user-visible reply text. Empty turns record ``"(stayed silent)"`` to
  distinguish "ran but said nothing" from "crashed".
- **``is_echo`` only guards when ``bot_wx_id`` is known.** iLink's
  ``getupdates`` returns inbound peer DMs, not our own sends, so an
  echo is unlikely — but if the bot's own wxid is set on the
  credential and matches ``sender_id``, treat it as an echo and drop.
- **``resolve_sender_name`` returns the wxid.** iLink has no
  user-info-by-id API, so the opaque wxid is the best label available —
  same shape as Telegram returning ``sender_id``.
- **History via the inbox, not the platform.** iLink has no server-side
  history API, so ``ChannelHistoryConfig(load_conversation_history=True,
  history_limit=20)`` leans on ``ChannelInboxWriter`` persisting every
  turn to ``bus_messages`` under ``channel_id=f"wechat_{to_user_id}"``,
  which ``WeChatContextBuilder`` reads back. Same contract as Telegram.

## First-DM owner claim (`_process_message` override)

The owner's wxid is **opaque until the first inbound DM**. Binding is
owner-initiated from the Channels panel (a QR scan), but the wxid is
not known at that moment — the QR handshake yields a session, not the
scanner's wxid. So owner identity is resolved at first-contact time, in
the trigger, not at bind time.

``_process_message`` checks: if ``credential.owner_wx_id`` is still
empty AND the inbound message has a ``sender_id``, call
``WeChatCredentialManager.claim_owner`` (a compare-and-set: it only
writes if the row's owner is still empty). On success it also mutates
the in-memory credential's ``owner_wx_id`` so the rest of THIS turn
already sees the resolved owner, then calls ``super()._process_message``.

**Security model — this IS "first DM wins".** Unlike Telegram (which
matches a pre-stored ``owner_username`` lock and is therefore *not*
first-DM-wins), WeChat has no handle to pre-lock against at bind time —
the wxid simply doesn't exist until someone DMs. The first DM after
binding claims ownership. The practical guard is that binding is an
owner-driven QR scan in their own panel, so in normal flow the owner
is the one who then sends the first DM. The CAS in ``claim_owner``
makes the claim idempotent and race-safe (only the first writer wins).

**Known residual risk (not yet addressed):** if anyone DMs the freshly
bound account *before* the owner does, they claim owner and the module
then surfaces owner-private context to them. The QR-bind flow makes this
unlikely in practice, but it is a real impersonation window — tracked in
``reference/self_notebook/todo/wechat-owner-claim-confirmation.md`` for a
future confirm-token gate. Deliberately left as-is for now.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase`` (shared channel trigger base —
  dedup, debounce, worker pool, audit, inbox).
- **Calls**: ``WeChatSDKClient.get_updates`` / ``aclose``,
  ``WeChatCredentialManager.list_active`` / ``claim_owner`` /
  ``set_enabled``, ``WeChatContextBuilder``, ``extract_text``.
- **Schemas read**: ``ParsedMessage``, ``ChatType``,
  ``MessageContentType``, ``WorkingSource.WECHAT``.
- **Reply path is NOT here**: replies go out through the
  ``wechat_send`` MCP tool (see ``_wechat_mcp_tools.md``); the trigger
  only reads that tool's call back out for the inbox.

## Gotchas

- Reverting ``extract_output`` to ``result.output_text`` re-creates the
  chain-of-thought leak (same regression Telegram's mirror warns
  about). Keep scraping the ``wechat_send`` ``text`` arg.
- ``self._cursors`` is per-agent and process-local. A restart starts
  each loop from an empty cursor — iLink's retention window governs how
  far back that replays; the base ``DEDUP`` store catches recent
  double-delivery, but very old re-delivered messages may double-process
  across a restart. Accept (same trade-off as Telegram's offset reset).
- ``connect`` advances the cursor *before* yielding the batch's
  messages, so an in-flight crash replays the batch on reconnect — the
  base dedup store is what makes that safe on the happy path.
- ``stop()`` closes every cached ``WeChatSDKClient`` with a 3s timeout;
  ``connect``'s ``finally`` also closes its own client and pops it from
  the cache. Don't remove either path — the cache close covers loops
  that ``stop()`` interrupts before ``connect``'s finally runs.
- ``claim_owner`` is a CAS — if the owner is already set it's a no-op.
  An owner who rebinds against a different scanner does NOT re-claim;
  the wxid stays whatever the first DM after the original bind set.
