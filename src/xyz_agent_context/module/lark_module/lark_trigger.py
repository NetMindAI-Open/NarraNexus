"""
@file_name: lark_trigger.py
@date: 2026-04-10
@description: Lark event trigger — subclass of ChannelTriggerBase.

Architecture: 1 WebSocket thread per bound bot + N shared async workers
(workers from the base class). The base class owns dedup, worker pool,
credential watcher, audit log, and inbox writing; this file owns the
Lark-specific 20%:

  - SDK WebSocket subscription with thread-local event loop proxy (H-6)
  - Bot open_id cache for echo filtering (M-6)
  - lark_cli tool-call output extraction (extract_output override)
  - IM-friendly error rendering (format_error_reply override)

Backward-compat shims kept for the 146 existing Lark tests:
``_dedup_and_enqueue``, ``_check_and_classify_event``,
``_should_process_event``, ``_process_message(cred, dict, worker_id)``,
``_seen_repo`` / ``_audit_repo`` / ``_seen_messages`` / ``_seen_lock``
properties, ``_write_to_inbox``. Phase 2.5 cleanup PR will migrate
tests to the base's public API and remove these shims.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any, AsyncIterator, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.run_collector import RunError, collect_run
from xyz_agent_context.channel.channel_audit_events import (
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_INBOX_WRITE_FAILED,
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_ECHO,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_OVERSIZED,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_INGRESS_PROCESSED,
    EVENT_TRANSPORT_BACKOFF,
    EVENT_TRANSPORT_CONNECTED,
    EVENT_TRANSPORT_DISCONNECTED,
)
from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig
from xyz_agent_context.channel.channel_dedup_store import ChannelDedupStore
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)
from xyz_agent_context.schema.attachment_schema import Attachment
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ParsedMessage
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now

from ._lark_credential_manager import LarkCredential, LarkCredentialManager
from .lark_cli_client import LarkCLIClient
from .lark_context_builder import LarkContextBuilder


# ──────────────────────────────────────────────────────────────────────────
# H-6 (2026-04-27): replace lark_oapi.ws.client.loop module global with a
# thread-local proxy.
#
# Background — what the SDK does:
# `lark_oapi.ws.client` defines a module-level `loop = asyncio.get_event_loop()`
# captured once at import time on the main thread. Every Client method then
# reads this global at every use:
#     loop.run_until_complete(self._connect())
#     loop.create_task(self._receive_message_loop())
#     loop.create_task(self._handle_message(msg))
# The SDK is implicitly designed for one Client per process.
#
# Why the previous M-9 patch was insufficient:
# NarraNexus runs N Client instances concurrently — one daemon thread per bot.
# The previous workaround patched `ws_mod.loop = fresh_loop` per thread under
# `_WS_LOOP_PATCH_LOCK`. The lock only covered the assignment, not the
# subsequent `ws_client.start()` call. After thread A released the lock,
# thread B could overwrite the global with `fresh_loop_B`. Thread A's
# `start()` then reads `loop` on every line, intermittently picking up
# thread B's loop, and the `_receive_message_loop` task ends up bound to a
# different loop than the websocket future it awaits. Result:
# `RuntimeError: Task got Future <Future pending> attached to a different loop`.
#
# Why the proxy is the correct fix:
# `asyncio.get_event_loop()` is already thread-local. By replacing the SDK's
# module global with a proxy that delegates every attribute access to
# `asyncio.get_event_loop()`, every SDK call from thread T resolves to thread
# T's own loop, with no shared mutable state across threads. _subscribe_loop
# only needs `asyncio.set_event_loop(fresh_loop)` once per thread — no
# module-level patching, no lock, no race window.
#
# This patch is applied once at module import time below; threads do nothing.
# ──────────────────────────────────────────────────────────────────────────
class _ThreadLocalLoopProxy:
    """Drop-in replacement for the lark_oapi SDK's module-level ``loop``.

    Resolves every attribute access (run_until_complete, create_task, time,
    etc.) to the calling thread's current asyncio event loop, eliminating
    the cross-thread race that caused
    ``RuntimeError: Future attached to a different loop``.
    """

    def __getattr__(self, name: str):
        return getattr(asyncio.get_event_loop(), name)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        try:
            return f"<_ThreadLocalLoopProxy bound to {asyncio.get_event_loop()!r}>"
        except RuntimeError:
            return "<_ThreadLocalLoopProxy (no loop on this thread)>"


def _install_lark_oapi_loop_proxy() -> None:
    """Install the proxy as ``lark_oapi.ws.client.loop``.

    Idempotent: if the proxy is already installed (e.g. on test reload),
    this is a no-op. Imported lazily so unit tests can monkey-patch the
    SDK before the proxy is installed.
    """
    import lark_oapi.ws.client as _ws_client_mod
    if not isinstance(_ws_client_mod.loop, _ThreadLocalLoopProxy):
        _ws_client_mod.loop = _ThreadLocalLoopProxy()


_install_lark_oapi_loop_proxy()


def _ws_loop_exception_filter(loop, context: dict) -> None:
    """asyncio loop exception-handler that turns WS-disconnect exceptions
    into a clean loop shutdown so the outer ``_subscribe_loop`` reconnect
    machinery can take over.

    The SDK fires ``loop.create_task(...)`` for incoming message handling
    and reconnect plumbing. When the upstream WS resets, those tasks raise
    ``ConnectionResetError`` / ``OSError`` / a ``websockets.*`` exception
    with no awaiter. The default asyncio handler would log "Task exception
    was never retrieved" + traceback per occurrence and then continue
    running the loop.

    But the SDK's ``ws.Client.start()`` itself blocks on a forever-sleeping
    ``_select()`` coroutine after spawning the receive task as
    fire-and-forget. With ``auto_reconnect=False`` (H-6) the receive task
    re-raises on disconnect, but that raise never propagates back to
    ``start()`` — the only thing keeping ``start()`` running is ``_select()``,
    which doesn't know anything died. Result: the daemon thread stays
    "alive" forever with no WS underneath. The 2026-05-18 EC2 zombie
    incident (11 h of silence + zero ``lark_trigger_audit`` rows) was
    exactly this shape.

    Fix: when this handler sees a connection-class exception, call
    ``loop.stop()``. That terminates ``loop.run_until_complete(_select())``,
    ``start()`` returns, ``run_ws`` exits, the daemon thread dies, and the
    outer ``while t.is_alive() and self.running`` poll in ``_subscribe_loop``
    falls through to the backoff + reconnect path.

    Unknown / unrelated exceptions (e.g. application bugs) still go to
    ``default_exception_handler`` so real bugs are not silenced. Contexts
    with no exception (e.g. slow-callback warnings) also pass through —
    the loop keeps running, which is correct for those.

    ``websockets.*`` exceptions are matched by module-name prefix rather
    than concrete type so we do not take a hard dependency on a specific
    SDK exception class.
    """
    exc = context.get("exception")
    if exc is None:
        loop.default_exception_handler(context)
        return
    if isinstance(exc, (ConnectionResetError, ConnectionError, OSError)):
        loop.stop()
        return
    if type(exc).__module__.startswith("websockets."):
        loop.stop()
        return
    loop.default_exception_handler(context)


# L-12: characters that must not survive into a sanitised display name.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _unescape_literal_escapes(text: str) -> str:
    """Convert literal escape sequences (``\\n``, ``\\t``, ``\\r``) back to
    real characters.

    Why this exists: when an LLM emits a tool call like
    ``lark_cli(command="im +messages-send --markdown \\"hello\\\\nworld\\"")``,
    the doubly-escaped ``\\\\n`` survives JSON deserialization as a literal
    backslash-n (two characters), not a real newline (0x0A). Lark client
    happens to render this correctly because lark-cli normalises it
    before sending, but our Inbox UI stores raw bytes and shows the
    literal characters. We normalise on extraction so the Inbox content
    matches what the user sees in Lark.

    Only common whitespace escapes are unwrapped — backslash followed by
    any other character is left alone.
    """
    if not text:
        return text
    # Order matters: unescape ``\\n`` (two chars) → ``\n`` (one char) first,
    # then process tabs and carriage returns. Skip processing if there's
    # no backslash at all.
    if "\\" not in text:
        return text
    return (
        text.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\r", "\r")
    )


def _compute_next_backoff(
    current: int,
    ran_seconds: float,
    *,
    base: int = 5,
    max_backoff: int = 120,
    healthy_threshold_seconds: int = 60,
) -> int:
    """Pick the next WS-reconnect backoff (H-1 fix).

    The previous loop had a dead ``backoff = 5`` line (no "ran > 60s" gate
    as its comment claimed) followed by an unconditional double, so backoff
    compounded toward the 120s cap after every disconnect — even after
    hours of healthy session.

    Now: if the session that just ended lasted at least
    ``healthy_threshold_seconds``, treat it as a real connection and reset
    to ``base``. Otherwise double, clamped to ``max_backoff``.

    Kept as a module-level function (not a base-class method) so existing
    tests can ``from .lark_trigger import _compute_next_backoff`` directly.
    """
    if ran_seconds >= healthy_threshold_seconds:
        return base
    return min(max(current, base) * 2, max_backoff)


def format_lark_error_reply(error: RunError) -> str:
    """Render an AgentRuntime failure as a Lark-friendly message.

    The sender in a Lark chat is often not the agent's owner (e.g. a
    colleague messaging a team bot). Showing them the raw developer
    error ("'agent' slot is not configured, go to Settings → Providers")
    is useless — they can't fix it. Instead we tell them what happened
    in plain language and point them at the owner.
    """
    etype = error.error_type
    if etype == "SystemDefaultUnavailable":
        return (
            "⚠️ I can't reply right now: the owner's free-quota tier is "
            "unavailable (disabled or exhausted). Please contact the "
            "bot's owner."
        )
    if etype == "LLMConfigNotConfigured":
        return (
            "⚠️ I can't reply right now: the owner hasn't finished "
            "configuring me. Please contact the bot's owner to set up "
            "an LLM provider or enable the free-quota tier in Settings."
        )
    return (
        "⚠️ I hit an internal error and can't reply to this message. "
        "Please try again in a bit, or contact the bot's owner."
    )


# ──────────────────────────────────────────────────────────────────────────
# LarkTrigger — subclass of ChannelTriggerBase
# ──────────────────────────────────────────────────────────────────────────

class LarkTrigger(ChannelTriggerBase):
    """SDK-WebSocket-driven Lark/Feishu trigger built on ``ChannelTriggerBase``.

    Each active + logged_in credential gets its own WebSocket thread.
    Events are dispatched to the base's shared async task queue processed
    by N workers. The base owns dedup / inbox / audit / cleanup / heartbeat;
    this class owns the SDK threading + Lark-specific echo / output
    extraction / error rendering.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "lark"
    brand_display = "Lark"  # Per-credential rendering picks Feishu vs Lark in inbox writer
    working_source = WorkingSource.LARK

    # ── Worker pool — same defaults as the base, kept here for tests ─────
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    # ── Lark dedup tunables — base defaults already 600 / 5min, but tests
    #    poke these as class attrs so we mirror them on the subclass.
    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Lark today does NOT debounce.
    DEBOUNCE_WINDOW_MS = 0

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            history_config=ChannelHistoryConfig(
                load_conversation_history=True,
                history_limit=20,
                history_max_chars=3000,
            ),
        )
        # Shared CLI for bot-info lookups, sender name resolution, and the
        # lark-cli send-message used for friendly error replies in
        # _build_and_run_agent.
        self._cli = LarkCLIClient()

        # Lark-specific lifecycle bookkeeping consumed by _health_server.
        self._last_ws_connected_monotonic: float = 0.0
        self._last_ws_connected_wallclock_ms: int = 0

        # Per-(agent_id, app_id) bot open_id cache for the 2-layer echo
        # filter. Keyed on a TUPLE because the same agent rebound to a
        # different app must NOT reuse the previous bot's open_id (M-6).
        self._bot_open_ids: dict[tuple[str, str], str] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle — start/stop
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        """Start trigger machinery + Lark health endpoint.

        Calls super().start() to bring up the worker pool / credential
        watcher / dedup store, then re-creates the dedup store with
        Lark-specific HISTORY_BUFFER_MS / DEDUP_TTL_SECONDS (the base
        defaults match today's Lark values, but expressing them on the
        subclass keeps tests that poke ``HISTORY_BUFFER_MS`` honest).
        """
        await super().start(db)

        # Re-create the dedup store with Lark-specific tunables.
        self._dedup_store = ChannelDedupStore(
            channel=self.channel_name,
            repo=ChannelSeenMessageRepository(self.channel_name, db),
            ttl_seconds=self.DEDUP_TTL_SECONDS,
            history_buffer_ms=self.HISTORY_BUFFER_MS,
        )
        self._dedup_store.update_baseline(self._startup_time_ms)

        # Lark-specific: bring up /healthz so operators can curl from
        # inside the container during incidents. Best-effort — trigger
        # still runs if the health server can't bind.
        from ._health_server import start_health_server
        health_task = await start_health_server(self)
        if health_task is not None:
            self._monitor_tasks.append(health_task)

        logger.info(
            f"LarkTrigger started: {len(self._workers)} workers, "
            f"watching for credentials"
        )

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations (PULL mode)
    # ────────────────────────────────────────────────────────────────────

    async def connect(self, credential: LarkCredential) -> AsyncIterator[dict]:
        """SDK WebSocket bridge.

        NOTE: Lark drives its own subscribe loop via the override below
        (``_subscribe_loop``) because the SDK's threaded callback model
        doesn't fit the base's ``async for raw in connect()`` shape
        cleanly. This method exists to satisfy the abstract contract; in
        practice it is never reached.
        """
        # pragma: no cover — overridden below
        raise NotImplementedError(
            "LarkTrigger overrides _subscribe_loop directly; connect() is "
            "kept only to satisfy the ABC."
        )
        yield  # type: ignore[unreachable]

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Convert a Lark dict event → ``ParsedMessage``.

        Lark content arrives as a JSON-encoded string whose shape depends
        on ``message_type``:

          - ``text``   → ``{"text": "..."}``                   — extract ``text``
          - ``post``   → ``{"<lang>": {"title", "content"}}``  — walk segments
          - ``file`` / ``image`` / ``audio`` / ``media`` /
            ``sticker``                                        — payload carries
            file/image metadata only, NO user-visible text. ``content`` MUST
            be empty (file metadata flows via ``raw["attachment_refs"]`` in
            Phase 1c, NOT through the prompt body).

        Historical bug pinned by ``tests/lark_module/test_lark_parse_event.py``:
        the previous implementation did ``json.loads(text).get("text", text)``
        for any content starting with ``{``. For file/image/etc messages
        there is no top-level ``text`` key, so the fallback returned the
        original JSON STRING — which then leaked into the agent's prompt
        as ``{"file_key":"...","file_name":"..."}`` gibberish. Phase 1c T9a
        fixes this by branching on ``message_type`` and routing each kind
        to a typed extractor.
        """
        msg_id = raw.get("message_id", raw.get("id", ""))
        chat_id = raw.get("chat_id", "")
        sender_id = raw.get("sender_id", "")
        sender_name = raw.get("sender_name", "Unknown")
        content_str = raw.get("content", "") or ""
        message_type = raw.get("message_type", "") or ""

        content = self._extract_user_visible_content(content_str, message_type)

        # Phase 1c T9b: extract attachment refs from media-type messages
        # so the base's _persist_attachment can download + store them.
        # Mutates a shallow copy of raw so the upstream caller's dict
        # stays untouched.
        refs = self._extract_attachment_refs(content_str, message_type, msg_id)
        if refs:
            raw = dict(raw)
            raw["attachment_refs"] = refs

        try:
            create_time_ms = int(raw.get("create_time", "0") or 0)
        except (ValueError, TypeError):
            create_time_ms = 0

        return ParsedMessage(
            message_id=msg_id,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content.strip(),
            timestamp_ms=create_time_ms,
            raw=raw,
        )

    @staticmethod
    def _extract_attachment_refs(
        content_str: str, message_type: str, msg_id: str
    ) -> list[dict]:
        """Parse Lark media-message content into normalized attachment refs.

        Returns a list of zero or one ref dicts (Lark messages carry at
        most one resource per ``message_type``). Each ref shape:

            {
                "kind":               "image" | "file" | "audio" | "media",
                "platform_ref":       <file_key or image_key>,
                "original_name":      <filename or synthesized>,
                "mime_hint":          <best guess; "" if unknown>,
                "size_hint":          <bytes or 0 if not provided>,
                "lark_message_id":    <msg_id>,        # needed for resource fetch URL
                "lark_resource_type": <"file"|"image"|"audio"|"media">,
            }

        ``sticker`` is intentionally skipped — Lark stickers are reusable
        platform assets (emoji-like), not user uploads, and there's no
        useful target for the agent's Read tool. text / post produce no
        refs (their content already lives in ``ParsedMessage.content``).

        Note on ``lark_resource_type``: Lark's "video+audio" container is
        ``media`` (which is what ``message_type`` reports). There is no
        Lark message_type named ``"video"`` at the time of writing — if
        Lark ever adds a pure-video type, both ``message_type`` and the
        resource_type would shift accordingly. Earlier docstring versions
        listed ``"video"`` here speculatively; removed to avoid implying
        the code emits a kind it never produces.
        """
        if message_type not in ("image", "file", "audio", "media"):
            return []
        if not content_str.startswith("{"):
            return []
        try:
            payload = json.loads(content_str)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []

        if message_type == "image":
            image_key = payload.get("image_key") or ""
            if not image_key:
                return []
            return [{
                "kind": "image",
                "platform_ref": image_key,
                # Lark doesn't ship a filename for inline images — synth
                # a name from the key so the Read marker reads naturally.
                "original_name": f"{image_key}.png",
                # Lark's inline images are PNG-encoded by default; libmagic
                # in _persist_attachment corrects the hint from the actual
                # bytes if the platform got it wrong.
                "mime_hint": "image/png",
                "size_hint": 0,
                "lark_message_id": msg_id,
                "lark_resource_type": "image",
            }]

        if message_type == "file":
            file_key = payload.get("file_key") or ""
            if not file_key:
                return []
            try:
                size_hint = int(payload.get("file_size", 0) or 0)
            except (ValueError, TypeError):
                size_hint = 0
            return [{
                "kind": "file",
                "platform_ref": file_key,
                "original_name": payload.get("file_name") or file_key,
                # Lark events don't include MIME; libmagic in
                # _persist_attachment will sniff from the actual bytes.
                "mime_hint": "",
                "size_hint": size_hint,
                "lark_message_id": msg_id,
                "lark_resource_type": "file",
            }]

        # audio / media (video-with-audio): payload carries file_key + duration.
        file_key = payload.get("file_key") or ""
        if not file_key:
            return []
        return [{
            "kind": message_type,
            "platform_ref": file_key,
            "original_name": payload.get("file_name") or f"{message_type}_{msg_id}",
            "mime_hint": "",
            "size_hint": 0,
            "lark_message_id": msg_id,
            "lark_resource_type": message_type,
        }]

    # Lark message_types that NEVER carry user-visible text in their payload.
    # The check below short-circuits these BEFORE inspecting payload["text"]
    # because some platforms occasionally include a ``text`` field for
    # display metadata (e.g. sticker descriptions, file captions in the
    # platform UI) that is NOT user-typed and MUST NOT enter the agent
    # prompt. Without this guard a sticker payload with
    # ``{"file_key":"stk_x","text":"smile"}`` would leak "smile" as if the
    # user had typed it. Defense: hard-gate on message_type first.
    _NO_USER_TEXT_MESSAGE_TYPES = frozenset({
        "image", "file", "audio", "media", "sticker",
    })

    @staticmethod
    def _extract_user_visible_content(content_str: str, message_type: str) -> str:
        """Pull the human-readable text out of a Lark ``content`` field.

        Returns ``""`` for media-only messages so their metadata never
        leaks into the agent prompt. Plain (non-JSON) ``content_str`` is
        passed through unchanged so legacy fixtures keep working.

        Order of checks is load-bearing:
          1. Hard-gate on ``_NO_USER_TEXT_MESSAGE_TYPES``. These types
             never produce user text regardless of what their payload
             carries.
          2. ``post`` → walk segments.
          3. Any other type with a top-level ``text`` field → use it.
             Covers ``text`` and any future Lark type whose payload
             still includes ``text``.
        """
        if not content_str:
            return ""
        if not content_str.startswith("{"):
            # Plain text content (legacy test fixtures, or non-JSON payloads).
            return content_str
        try:
            payload = json.loads(content_str)
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        if message_type in LarkTrigger._NO_USER_TEXT_MESSAGE_TYPES:
            # Known media — payload may carry a ``text`` field (sticker
            # description, file caption metadata) but it is NOT user
            # input. Refuse to surface it.
            return ""
        if message_type == "post":
            return LarkTrigger._extract_post_text(payload)
        if "text" in payload:
            return payload.get("text", "") or ""
        return ""

    @staticmethod
    def _extract_post_text(payload: dict) -> str:
        """Flatten a Lark ``post``-type payload to a single text string.

        Lark post payloads are multi-language:
            {"zh_cn": {"title": "...", "content": [[seg, seg], [seg]]}}
        Each line is a list of segments, each segment is a dict possibly
        carrying a ``text`` field. We return the first non-empty language
        block's title + concatenated segment texts. Mirrors the walker in
        ``_preview_message_content`` (kept separate so the preview helper
        can stay frozen — refactoring it risks regressing the audit log).
        """
        for lang_block in payload.values():
            if not isinstance(lang_block, dict):
                continue
            title = lang_block.get("title", "") or ""
            body_bits: list[str] = []
            for line in lang_block.get("content", []) or []:
                if not isinstance(line, list):
                    continue
                for seg in line:
                    if isinstance(seg, dict):
                        body_bits.append(seg.get("text", "") or "")
            text = (title + " " + " ".join(body_bits)).strip()
            if text:
                return text
        return ""

    # ────────────────────────────────────────────────────────────────────
    # Phase 1c T9c: attachment ingest override
    # ────────────────────────────────────────────────────────────────────

    async def fetch_attachments(  # type: ignore[override]
        self, message: ParsedMessage, credential: LarkCredential
    ) -> list[Attachment]:
        """Download every Lark resource ref via lark-cli and persist.

        Never-raises. Per-ref failures (CLI error, oversized, persist
        error) are audited as ``EVENT_ATTACHMENT_FETCH_FAILED`` or
        ``EVENT_INGRESS_DROPPED_OVERSIZED`` and skipped; remaining refs
        still flow. Mirrors the contract on
        ``ChannelTriggerBase.fetch_attachments``.
        """
        refs = (message.raw or {}).get("attachment_refs") or []
        if not refs:
            return []

        from backend.config import settings as backend_settings
        max_bytes = backend_settings.max_upload_bytes

        out: list[Attachment] = []
        for ref in refs:
            platform_ref = ref.get("platform_ref") or ""
            if not platform_ref:
                continue
            lark_message_id = ref.get("lark_message_id") or message.message_id
            resource_type = ref.get("lark_resource_type") or ref.get("kind") or "file"
            original_name = ref.get("original_name") or platform_ref
            mime_hint = ref.get("mime_hint", "") or ""
            try:
                size_hint = int(ref.get("size_hint", 0) or 0)
            except (ValueError, TypeError):
                size_hint = 0

            # Pre-check backend cap (only available when message_type=file).
            if size_hint and size_hint > max_bytes:
                logger.info(
                    f"[lark:{credential.agent_id}] refusing oversized "
                    f"attachment {original_name!r}: size_hint={size_hint} "
                    f"> max_upload_bytes={max_bytes}"
                )
                await self._audit(
                    EVENT_INGRESS_DROPPED_OVERSIZED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "size_hint": size_hint,
                        "max_upload_bytes": max_bytes,
                        "reason": "backend_max_upload_bytes",
                    },
                )
                continue

            try:
                raw_bytes = await self._cli.fetch_message_resource(
                    credential.agent_id,
                    message_id=lark_message_id,
                    file_key=platform_ref,
                    resource_type=resource_type,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[lark:{credential.agent_id}] resource fetch failed for "
                    f"{platform_ref!r}: {type(e).__name__}: {e}"
                )
                await self._audit(
                    EVENT_ATTACHMENT_FETCH_FAILED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "stage": "fetch_message_resource",
                        "error": f"{type(e).__name__}:{e}",
                    },
                )
                continue

            # Post-download cap (lark-cli has no streaming mode; the
            # bytes are already on disk by the time we check size).
            if len(raw_bytes) > max_bytes:
                logger.info(
                    f"[lark:{credential.agent_id}] dropping oversized "
                    f"downloaded attachment {original_name!r}: "
                    f"{len(raw_bytes)} > {max_bytes}"
                )
                await self._audit(
                    EVENT_INGRESS_DROPPED_OVERSIZED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "actual_bytes": len(raw_bytes),
                        "max_upload_bytes": max_bytes,
                        "reason": "post_download_cap",
                    },
                )
                continue

            try:
                att = await self._persist_attachment(
                    agent_id=credential.agent_id,
                    raw_bytes=raw_bytes,
                    original_name=original_name,
                    mime_hint=mime_hint,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[lark:{credential.agent_id}] persist failed for "
                    f"{original_name!r}: {type(e).__name__}: {e}"
                )
                await self._audit(
                    EVENT_ATTACHMENT_FETCH_FAILED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "stage": "persist",
                        "error": f"{type(e).__name__}:{e}",
                    },
                )
                continue

            out.append(att)
            await self._audit(
                EVENT_ATTACHMENT_PERSISTED,
                message_id=message.message_id,
                agent_id=credential.agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "file_id": att.file_id,
                    "mime_type": att.mime_type,
                    "size_bytes": att.size_bytes,
                    "category": att.category.value,
                    "has_transcript": bool(att.transcript),
                },
            )
        return out

    async def _is_echo(
        self, credential: LarkCredential, raw: dict, sender_id: str = ""
    ) -> bool:
        """Backward-compat shim for tests that call ``_is_echo`` directly
        with the legacy ``(cred, raw_dict, sender_id)`` signature.

        Production path uses the abstract ``is_echo(message, credential)``
        below.
        """
        # Build a minimal ParsedMessage carrying just the raw dict + sender.
        message = ParsedMessage(
            message_id=raw.get("message_id", "") or "",
            chat_id=raw.get("chat_id", "") or "",
            sender_id=sender_id or raw.get("sender_id", "") or "",
            sender_name=raw.get("sender_name", "Unknown") or "Unknown",
            content="",
            raw=raw,
        )
        return await self.is_echo(message, credential)

    async def is_echo(
        self, message: ParsedMessage, credential: LarkCredential
    ) -> bool:
        """Two-layer echo filter for Lark.

        Layer 1 (cheap): the SDK event tells us if the sender was a bot/app.
        Layer 2 (one API call, cached): match sender_id against this bot's
        own open_id. Cache key is ``(agent_id, app_id)`` so a re-bind to a
        different app cannot reuse the previous bot's identity (M-6).
        """
        sender_type = message.raw.get("sender_type", "")
        if sender_type in ("bot", "app"):
            return True

        cache_key = (credential.agent_id, credential.app_id)
        if cache_key not in self._bot_open_ids:
            try:
                bot_info = await self._cli._run_with_agent_id(
                    ["api", "GET", "/open-apis/bot/v3/info"],
                    credential.agent_id,
                )
                if bot_info.get("success"):
                    bot_oid = (
                        bot_info.get("data", {})
                        .get("bot", {})
                        .get("open_id", "")
                    )
                    if bot_oid:
                        self._bot_open_ids[cache_key] = bot_oid
            except Exception:
                logger.debug(
                    f"Failed to fetch bot open_id for {credential.profile_name}"
                )
        bot_oid = self._bot_open_ids.get(cache_key, "")
        return bool(bot_oid and message.sender_id == bot_oid)

    async def resolve_sender_name(
        self, sender_id: str, credential: LarkCredential
    ) -> str:
        """Resolve a sender's display name via the owner's user token.

        Best-effort — returns 'Unknown' on miss. We look up names with
        `--as user` (see ``LarkCLIClient.get_user``); the bot tenant token
        can't read names. An agent that never completed the three-click
        user authorization has no user token, so we skip the doomed CLI
        call and stay 'Unknown' rather than spawning a subprocess that can
        only fail.
        """
        if not credential.user_oauth_ok():
            return "Unknown"
        try:
            user_info = await self._cli.get_user(
                credential.agent_id, user_id=sender_id, identity="user"
            )
            if user_info.get("success"):
                outer = user_info.get("data", {})
                inner = outer.get("data", outer)
                user_obj = inner.get("user", inner)
                return (
                    user_obj.get("name")
                    or user_obj.get("en_name")
                    or user_obj.get("email", "")
                    .split("@")[0]
                    .replace(".", " ")
                    .title()
                    or "Unknown"
                )
        except Exception:
            logger.debug(f"Failed to resolve sender name for {sender_id}")
        return "Unknown"

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: LarkCredential,
        agent_id: str,
    ) -> LarkContextBuilder:
        """Wrap LarkContextBuilder with Lark-shaped event dict."""
        # The existing LarkContextBuilder consumes a flat dict for `event`
        # with already-parsed text content (NOT JSON-encoded).
        normalized = dict(message.raw)
        normalized.update({
            "chat_id": message.chat_id,
            "chat_type": message.raw.get("chat_type", "p2p"),
            "chat_name": message.raw.get("chat_name", ""),
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "content": message.content,
            "message_id": message.message_id,
            "create_time": str(message.timestamp_ms),
        })
        return LarkContextBuilder(
            event=normalized,
            credential=credential,
            cli=self._cli,
            agent_id=agent_id,
        )

    async def load_active_credentials(self) -> list[LarkCredential]:
        """Active + logged_in Lark credentials from DB."""
        mgr = LarkCredentialManager(self._db)
        return await mgr.get_active_credentials()

    # ────────────────────────────────────────────────────────────────────
    # Subscribe loop — Lark's SDK threading does not fit the base's
    # async-iterator pattern. We override entirely.
    # ────────────────────────────────────────────────────────────────────

    async def _subscribe_loop(self, cred: LarkCredential) -> None:
        """Run SDK WebSocket subscription for one bot. Restart on failure
        with backoff.

        The SDK's ``ws.Client.start()`` internally runs its own asyncio
        event loop, so it must run in a separate thread with NO existing
        event loop. We use ``threading.Thread`` (not ``asyncio.to_thread``)
        to ensure a clean thread without an inherited event loop.

        Re-reads the credential from DB at each iteration so that if the
        user corrects a wrong App Secret via ``lark_enable_receive`` (or
        updates via re-bind), the next retry picks up the fresh value
        instead of looping forever against stale state.
        """
        import lark_oapi as lark

        agent_id = cred.agent_id
        app_id_initial = cred.app_id
        backoff = 5
        max_backoff = 120
        ws_start_monotonic: float = 0.0

        while self.running:
            # Refresh the credential from DB each iteration.
            fresh_cred = await LarkCredentialManager(self._db).get_credential(agent_id)
            if not fresh_cred or not fresh_cred.is_active:
                logger.info(
                    f"LarkTrigger: credential gone or inactive for {agent_id}, "
                    f"exiting subscriber"
                )
                return
            if fresh_cred.app_id != app_id_initial:
                logger.info(
                    f"LarkTrigger: app_id changed for {agent_id} "
                    f"({app_id_initial} -> {fresh_cred.app_id}); exiting so the "
                    f"watcher can start a fresh subscriber"
                )
                return
            app_secret = fresh_cred.get_app_secret()
            if not app_secret:
                logger.warning(
                    f"LarkTrigger: App Secret cleared for {fresh_cred.profile_name}; "
                    f"exiting subscriber"
                )
                return
            cred = fresh_cred

            try:
                # SDK callback: runs in SDK's thread. Hand the event off
                # to an async coroutine on the main loop; that coroutine
                # runs the dedup cascade (memory + DB + historic baseline)
                # and only enqueues for workers when checks clear.
                def on_message(data):
                    try:
                        event_dict = self._sdk_event_to_dict(data)
                        if not event_dict:
                            return
                        asyncio.run_coroutine_threadsafe(
                            self._dedup_and_enqueue(cred, event_dict),
                            self._loop,
                        )
                    except Exception as e:
                        logger.warning(f"LarkTrigger SDK callback error: {e}")

                # Register message-read receipts with a no-op processor so
                # the SDK does not flood ERROR with "processor not found,
                # type: im.message.message_read_v1" — Lark pushes one per
                # IM read by default and we don't act on read receipts.
                def _on_message_read(_data):
                    pass

                handler = self._build_event_handler(on_message, _on_message_read)

                domain = (
                    lark.LARK_DOMAIN if cred.brand == "lark" else lark.FEISHU_DOMAIN
                )
                # auto_reconnect=False (H-6 fix, 2026-04-27): the SDK's
                # internal _reconnect() does not re-patch
                # lark_oapi.ws.client.loop after a keepalive timeout, so the
                # second connection's futures get bound to a different loop
                # than the _receive_message_loop task. Letting the SDK raise
                # on first disconnect lets the outer ``while self.running``
                # loop here own the reconnect (with backoff + fresh
                # credentials + audit rows).
                ws_client = lark.ws.Client(
                    app_id=cred.app_id,
                    app_secret=app_secret,
                    event_handler=handler,
                    domain=domain,
                    auto_reconnect=False,
                )

                logger.info(
                    f"LarkTrigger: connecting SDK WebSocket for {cred.profile_name}"
                )

                thread_error: list[Exception] = []

                def run_ws():
                    try:
                        # H-6 (2026-04-27): module-level proxy installed at
                        # `lark_oapi.ws.client.loop` makes every SDK access
                        # of `loop` resolve to the calling thread's current
                        # asyncio loop.
                        fresh_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(fresh_loop)
                        # Silence "Task exception was never retrieved"
                        # noise for SDK-internal fire-and-forget tasks
                        # that raise on WS disconnect (the outer reconnect
                        # loop owns the recovery).
                        fresh_loop.set_exception_handler(
                            _ws_loop_exception_filter
                        )
                        ws_client._lock = asyncio.Lock()
                        ws_client.start()
                    except Exception as e:
                        thread_error.append(e)

                t = threading.Thread(target=run_ws, daemon=True)
                ws_start_monotonic = time.monotonic()
                t.start()

                # Note the moment the WS is considered "up" — the historic-
                # replay filter (H-5) uses this so a long disconnect followed
                # by reconnect won't silently let Lark's backlog of old
                # events through.
                self._last_ws_connected_monotonic = ws_start_monotonic
                self._last_ws_connected_wallclock_ms = int(time.time() * 1000)
                if self._dedup_store is not None:
                    self._dedup_store.update_baseline(
                        self._last_ws_connected_wallclock_ms
                    )
                await self._audit(
                    EVENT_TRANSPORT_CONNECTED,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={
                        "profile_name": cred.profile_name,
                        "brand": cred.brand,
                    },
                )

                while t.is_alive() and self.running:
                    await asyncio.sleep(1)

                if thread_error:
                    raise thread_error[0]

                ran_seconds = time.monotonic() - ws_start_monotonic
                if not t.is_alive():
                    backoff = _compute_next_backoff(
                        current=backoff,
                        ran_seconds=ran_seconds,
                        max_backoff=max_backoff,
                    )
                    logger.warning(
                        f"LarkTrigger SDK WebSocket disconnected for "
                        f"{cred.profile_name} after {ran_seconds:.1f}s; "
                        f"restarting in {backoff}s"
                    )
                    await self._audit(
                        EVENT_TRANSPORT_DISCONNECTED,
                        agent_id=cred.agent_id,
                        app_id=cred.app_id,
                        details={
                            "ran_seconds": ran_seconds,
                            "next_backoff_seconds": backoff,
                        },
                    )
            except asyncio.CancelledError:
                logger.info(
                    f"LarkTrigger: subscriber cancelled for {cred.profile_name}"
                )
                return
            except Exception as e:
                ran_seconds = (
                    time.monotonic() - ws_start_monotonic
                    if ws_start_monotonic > 0 else 0.0
                )
                # Brand-mismatch detection (B.1 in the lark-binding-wizard
                # work): error code 1000040351 = "Incorrect domain name" =
                # the user selected one platform (Feishu/Lark) but the
                # App ID is registered on the other. Re-trying in a hot
                # loop will just hit the same error every backoff cycle,
                # so we mark the credential as brand_mismatch (excluded
                # from AUTH_STATUSES_BOT_ACTIVE → trigger skips it on
                # next watcher tick) and surface the state to UI + agent.
                # See _lark_credential_manager for the full state list.
                err_text = f"{type(e).__name__}: {e}"
                if "1000040351" in err_text or "Incorrect domain" in err_text:
                    # LarkCredentialManager is already imported at module level
                    # (line 60). Re-importing it inside this function makes
                    # Python treat the name as a *local* throughout the whole
                    # function scope — so line 580's `await
                    # LarkCredentialManager(self._db).get_credential(...)` hit
                    # UnboundLocalError on the very first iteration of the
                    # subscribe loop (the LarkTrigger crash in 2026-05-27
                    # dmg + cloud logs). Only AUTH_STATUS_BRAND_MISMATCH is
                    # the genuinely lazy import here.
                    from ._lark_credential_manager import AUTH_STATUS_BRAND_MISMATCH
                    try:
                        mgr = LarkCredentialManager(self.db)
                        await mgr.update_auth_status(
                            cred.agent_id, AUTH_STATUS_BRAND_MISMATCH
                        )
                        logger.warning(
                            f"LarkTrigger: brand mismatch detected for "
                            f"{cred.profile_name} (agent={cred.agent_id}). "
                            f"Marked auth_status=brand_mismatch so the watcher "
                            f"won't restart this subscriber. User must re-bind."
                        )
                    except Exception as inner_exc:  # noqa: BLE001
                        logger.exception(
                            f"LarkTrigger: failed to persist brand_mismatch "
                            f"for {cred.profile_name}: {inner_exc}"
                        )
                    await self._audit(
                        EVENT_TRANSPORT_DISCONNECTED,
                        agent_id=cred.agent_id,
                        app_id=cred.app_id,
                        details={
                            "ran_seconds": ran_seconds,
                            "error": err_text,
                            "auth_status_set": "brand_mismatch",
                            "next_backoff_seconds": 0,
                        },
                    )
                    # Skip backoff/restart — the next watcher tick will
                    # see auth_status not in AUTH_STATUSES_BOT_ACTIVE and
                    # leave this credential alone until user re-binds.
                    return
                backoff = _compute_next_backoff(
                    current=backoff,
                    ran_seconds=ran_seconds,
                    max_backoff=max_backoff,
                )
                logger.exception(
                    f"LarkTrigger SDK error for {cred.profile_name} "
                    f"after {ran_seconds:.1f}s (next backoff {backoff}s): {e}"
                )
                await self._audit(
                    EVENT_TRANSPORT_DISCONNECTED,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={
                        "ran_seconds": ran_seconds,
                        "next_backoff_seconds": backoff,
                        "error": err_text,
                    },
                )

            if not self.running:
                break

            await self._audit(
                EVENT_TRANSPORT_BACKOFF,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                details={"sleep_seconds": backoff},
            )
            await asyncio.sleep(backoff)

    async def _stop_subscriber(self, key: str) -> None:
        """Override base to clear bot_open_id cache (M-6)."""
        cred = self._subscriber_creds.get(key)
        await super()._stop_subscriber(key)
        # M-6: clear the bot_open_id cache so a later rebind of the same
        # agent to a different app doesn't reuse stale identity.
        if cred is not None:
            self._bot_open_ids.pop((cred.agent_id, cred.app_id), None)

    # ────────────────────────────────────────────────────────────────────
    # SDK event helpers
    # ────────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_event_handler(on_message_receive, on_message_read):
        """Build the Lark `EventDispatcherHandler` for our subscription.

        Extracted from `_subscribe_loop` so the processor registration
        can be asserted in isolation (see tests/lark_module/
        test_message_read_handler.py).

        ``lark_oapi.EventDispatcherHandler`` uses a strict-whitelist
        dispatch model: any received event whose type is not in the
        ``_processorMap`` raises ``EventException("processor not
        found, ...")`` which the SDK logs as ERROR. There is no default
        / catch-all handler. Lark pushes events to the bot based on
        the app's subscription config — for any IM scope the user
        granted, multiple event types can fire (reactions, recalls,
        group member changes, etc.) regardless of whether we want to
        act on them.

        We register the full set of IM events the SDK knows about.
        ``on_message_receive`` carries the real handler; everything
        else (including ``message_read_v1`` per the original 2026-05-19
        fix) is a no-op whose only purpose is to keep the SDK quiet.
        """
        import lark_oapi as lark

        def _noop(_data):  # pragma: no cover — defensive ledger only
            pass

        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .register_p2_im_message_message_read_v1(on_message_read)
            # Defensive no-ops — without these the SDK logs an ERROR
            # for every received event of these types. We never act on
            # any of them today.
            .register_p2_im_message_recalled_v1(_noop)
            .register_p2_im_message_reaction_created_v1(_noop)
            .register_p2_im_message_reaction_deleted_v1(_noop)
            .register_p2_im_chat_disbanded_v1(_noop)
            .register_p2_im_chat_updated_v1(_noop)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_noop)
            .register_p2_im_chat_member_bot_added_v1(_noop)
            .register_p2_im_chat_member_bot_deleted_v1(_noop)
            .register_p2_im_chat_member_user_added_v1(_noop)
            .register_p2_im_chat_member_user_deleted_v1(_noop)
            .register_p2_im_chat_member_user_withdrawn_v1(_noop)
            .build()
        )

    @staticmethod
    def _sdk_event_to_dict(data) -> dict:
        """Convert lark-oapi P2ImMessageReceiveV1 event → flat dict.

        Kept as a backward-compat helper — tests construct dicts via this
        signature.
        """
        try:
            event = data.event
            sender = event.sender
            message = event.message
            return {
                "type": "im.message.receive_v1",
                "chat_id": message.chat_id or "",
                "chat_type": message.chat_type or "p2p",
                "message_id": message.message_id or "",
                "sender_id": (
                    sender.sender_id.open_id if sender and sender.sender_id else ""
                ),
                "sender_type": sender.sender_type or "" if sender else "",
                "content": message.content or "",
                "message_type": message.message_type or "text",
                "create_time": message.create_time or "",
            }
        except Exception as e:
            logger.warning(f"LarkTrigger: failed to convert SDK event: {e}")
            return {}

    @staticmethod
    def _preview_message_content(raw_content: str, message_type: str) -> str:
        """Render a short, log-safe preview of a Lark message payload.

        Lark stores message content as a JSON-encoded string whose shape
        depends on ``message_type`` (text → ``{"text": "..."}``, post →
        rich segments, file → ``{"file_key": "...", ...}``). For
        observability we want the human-readable gist, capped at 160 chars.
        """
        if not raw_content:
            return ""
        try:
            payload = json.loads(raw_content)
        except Exception:
            payload = None

        text = ""
        if isinstance(payload, dict):
            if message_type == "text":
                text = payload.get("text", "") or ""
            elif message_type == "post":
                for lang_block in payload.values():
                    if isinstance(lang_block, dict):
                        title = lang_block.get("title", "") or ""
                        body_bits = []
                        for line in lang_block.get("content", []) or []:
                            if isinstance(line, list):
                                for seg in line:
                                    if isinstance(seg, dict):
                                        body_bits.append(seg.get("text", "") or "")
                        text = (title + " " + " ".join(body_bits)).strip()
                        if text:
                            break
            elif message_type in ("file", "image", "audio", "media"):
                text = (
                    payload.get("file_name")
                    or payload.get("image_key")
                    or payload.get("file_key")
                    or ""
                )
            else:
                for v in payload.values():
                    if isinstance(v, str) and v:
                        text = v
                        break
        if not text:
            text = raw_content

        flattened = " ".join(text.split())
        return flattened[:160]

    # ────────────────────────────────────────────────────────────────────
    # Backward-compat shims for existing tests — production path uses the
    # base's _dedup_and_handle / _process_message directly via the worker.
    # ────────────────────────────────────────────────────────────────────

    async def _dedup_and_enqueue(self, cred, event_dict: dict) -> None:
        """Backward-compat shim. Tests construct dict events directly.

        The richer audit detail (content_preview, message_type, chat_type)
        is written here BEFORE delegating to the base, because the base's
        ``_dedup_and_handle`` writes a leaner ``details`` dict.
        """
        msg_id = event_dict.get("message_id", "")
        chat_id = event_dict.get("chat_id", "")
        chat_type = event_dict.get("chat_type", "")
        sender_id = event_dict.get("sender_id", "")
        message_type = event_dict.get("message_type", "")
        content_preview = self._preview_message_content(
            event_dict.get("content", ""), message_type
        )

        logger.info(
            "LarkTrigger ingress | agent={agent} app={app} <- from={sender} "
            "chat={chat}({chat_type}) msg_id={msg_id} type={msg_type} "
            "preview={preview!r}",
            agent=cred.agent_id,
            app=cred.app_id,
            sender=sender_id or "<unknown>",
            chat=chat_id or "<unknown>",
            chat_type=chat_type or "<unknown>",
            msg_id=msg_id or "<unknown>",
            msg_type=message_type or "<unknown>",
            preview=content_preview,
        )

        ingress_details = {
            "message_type": message_type,
            "chat_type": chat_type,
            "content_preview": content_preview,
        }

        decision = await self._check_and_classify_event(event_dict)

        if decision["accept"]:
            await self._audit(
                EVENT_INGRESS_PROCESSED,
                message_id=msg_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={"dedup_layer": decision["layer"], **ingress_details},
            )
            if decision["layer"] == "db_fail_open":
                await self._audit(
                    EVENT_DEDUP_FAIL_OPEN,
                    message_id=msg_id,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={"error": decision.get("error", "")},
                )
            # Parse to ParsedMessage before enqueueing so the base's
            # _worker can read `.message_id` for audit details on
            # timeout / error paths. The _process_message override still
            # accepts dict input for legacy test calls.
            parsed = self.parse_event(event_dict)
            if parsed is not None:
                await self._task_queue.put((cred, parsed))
        else:
            event_name = {
                "historic": EVENT_INGRESS_DROPPED_HISTORIC,
                "memory_dedup": EVENT_INGRESS_DROPPED_DEDUP,
                "db_dedup": EVENT_INGRESS_DROPPED_DEDUP,
            }.get(decision["layer"], EVENT_INGRESS_DROPPED_DEDUP)
            await self._audit(
                event_name,
                message_id=msg_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={"layer": decision["layer"], **ingress_details},
            )
            logger.info(
                f"LarkTrigger: dedup skipping message_id={msg_id!r} "
                f"(layer={decision['layer']})"
            )

    async def _check_and_classify_event(self, event_dict: dict) -> dict:
        """Delegate to the dedup store. Returns same dict shape as before.

        Layers:
          1. Historic-replay filter (timestamp baseline)
          2. In-memory hot cache (TTL-bounded)
          3. Durable DB gate (survives restarts)

        Baseline = ``max(_startup_time_ms, _last_ws_connected_wallclock_ms)``.
        Tests directly poke ``_startup_time_ms`` after constructing the
        trigger, so we sync those values into the dedup store on every
        call (``update_baseline`` is monotonic).
        """
        msg_id = event_dict.get("message_id", "")
        try:
            create_time_ms = int(event_dict.get("create_time", "0") or 0)
        except (ValueError, TypeError):
            create_time_ms = 0

        if self._dedup_store is None:
            return {"accept": True, "layer": "no_repo"}

        # Sync baseline with current Lark-side bookkeeping. Monotonic, so
        # repeated calls are cheap.
        if self._startup_time_ms:
            self._dedup_store.update_baseline(self._startup_time_ms)
        if self._last_ws_connected_wallclock_ms:
            self._dedup_store.update_baseline(self._last_ws_connected_wallclock_ms)

        return await self._dedup_store.classify(msg_id, create_time_ms)

    async def _should_process_event(self, event_dict: dict) -> bool:
        """Bool wrapper kept for test compatibility."""
        decision = await self._check_and_classify_event(event_dict)
        return decision["accept"]

    # ────────────────────────────────────────────────────────────────────
    # Worker pipeline overrides
    # ────────────────────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        """Override — base's worker uses ``message.message_id`` for audit
        on timeout/error, which fails when tests put raw dict events on
        the queue. We extract message_id defensively."""
        from xyz_agent_context.channel.channel_audit_events import (
            EVENT_WORKER_ERROR,
            EVENT_WORKER_TIMEOUT,
        )
        while self.running:
            try:
                credential, message = await asyncio.wait_for(
                    self._task_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            def _msg_id(m) -> str:
                if isinstance(m, dict):
                    return m.get("message_id", "")
                return getattr(m, "message_id", "")

            def _chat_id(m) -> str:
                if isinstance(m, dict):
                    return m.get("chat_id", "")
                return getattr(m, "chat_id", "")

            try:
                await asyncio.wait_for(
                    self._process_message(credential, message),
                    timeout=self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.exception(
                    f"LarkTrigger worker {worker_id} message "
                    f"{_msg_id(message)!r} exceeded "
                    f"{self.PROCESS_MESSAGE_TIMEOUT_SECONDS}s — cancelling"
                )
                await self._audit(
                    EVENT_WORKER_TIMEOUT,
                    message_id=_msg_id(message),
                    agent_id=getattr(credential, "agent_id", ""),
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=_chat_id(message),
                    details={
                        "worker_id": worker_id,
                        "timeout_seconds": self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    f"LarkTrigger worker {worker_id} error: {e}"
                )
                await self._audit(
                    EVENT_WORKER_ERROR,
                    message_id=_msg_id(message),
                    agent_id=getattr(credential, "agent_id", ""),
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=_chat_id(message),
                    details={
                        "worker_id": worker_id,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    async def _process_message(
        self, cred: LarkCredential, event_or_message, worker_id: int = 0
    ) -> None:
        """Process one message. Accepts dict (from _dedup_and_enqueue shim
        and existing tests) OR ParsedMessage (when an upstream caller has
        already parsed)."""
        # Cred gatekeeper — events from unbound credentials reach this
        # point if a subscriber crashed mid-stream. Reject so the agent
        # never runs against a bot the user has unbound.
        if cred.app_id not in self._subscriber_creds:
            msg_id_unbound = (
                event_or_message.get("message_id", "")
                if isinstance(event_or_message, dict)
                else getattr(event_or_message, "message_id", "")
            )
            chat_id_unbound = (
                event_or_message.get("chat_id", "")
                if isinstance(event_or_message, dict)
                else getattr(event_or_message, "chat_id", "")
            )
            logger.info(
                f"LarkTrigger worker {worker_id}: dropping event from "
                f"unbound credential (agent_id={cred.agent_id}, "
                f"app_id={cred.app_id}); msg_id={msg_id_unbound!r}"
            )
            await self._audit(
                EVENT_INGRESS_DROPPED_UNBOUND,
                message_id=msg_id_unbound,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id_unbound,
                details={"worker_id": worker_id},
            )
            return

        if isinstance(event_or_message, dict):
            parsed = self.parse_event(event_or_message)
            if parsed is None:
                return
            message = parsed
        else:
            message = event_or_message

        # Echo filter — Lark-specific 2-layer
        if await self.is_echo(message, cred):
            await self._audit(
                EVENT_INGRESS_DROPPED_ECHO,
                message_id=message.message_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
            )
            return

        # Empty-content guard: drop only when there's NEITHER text NOR
        # attachment_refs. Phase 1c made files first-class — a user can
        # drag in a PDF/image/audio with no caption (very common) and the
        # upload MUST still trigger the agent. Mirrors the same fix
        # applied to ChannelTriggerBase._process_message in
        # ``a00adbd fix(channel): allow caption-less file uploads``.
        has_refs = bool((message.raw or {}).get("attachment_refs"))
        if (not message.content or not message.content.strip()) and not has_refs:
            return

        # Resolve sender name + sanitize
        sender_name = message.sender_name
        if (not sender_name or sender_name == "Unknown") and message.sender_id:
            sender_name = await self.resolve_sender_name(message.sender_id, cred)
        sender_name = self.sanitize_display_name(sender_name)
        message.sender_name = sender_name

        logger.info(
            f"LarkTrigger [{cred.profile_name}] message from "
            f"{sender_name} ({message.sender_id}): {message.content[:100]}"
        )

        # Phase 1c T9d: attachment ingest. Never-raise — failures degrade
        # to a text-only agent run while preserving the audit trail.
        attachments: list[Attachment] = []
        try:
            attachments = await self.fetch_attachments(message, cred)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"LarkTrigger [{cred.profile_name}] fetch_attachments raised: "
                f"{type(e).__name__}: {e}"
            )
            await self._audit(
                EVENT_ATTACHMENT_FETCH_FAILED,
                message_id=message.message_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"error": f"{type(e).__name__}: {e}"},
            )

        # Build context, run agent, get output text
        output_text = await self._build_and_run_agent(
            cred, message, sender_name, attachments=attachments,
        )

        # Write to inbox via the channel writer
        try:
            await self._inbox_writer.write(
                db=self._db,
                agent_id=cred.agent_id,
                sender_id=message.sender_id,
                sender_name=sender_name,
                original_message=message.content,
                agent_response=output_text,
                chat_id=message.chat_id,
            )
        except Exception as e:  # noqa: BLE001
            await self._audit(
                EVENT_INBOX_WRITE_FAILED,
                message_id=message.message_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "error": f"{type(e).__name__}: {e}",
                    "sender_name": sender_name,
                    "original_message": message.content[:500],
                    "agent_response": (output_text or "")[:500],
                },
            )

    async def _build_and_run_agent(
        self,
        cred: LarkCredential,
        message: Optional[ParsedMessage] = None,
        sender_name: Optional[str] = None,
        # Backward-compat keyword arguments for tests that pass the old
        # 7-arg signature. When `message` is None, these are used to
        # construct a ParsedMessage on the fly.
        event: Optional[dict] = None,
        chat_id: Optional[str] = None,
        sender_id: Optional[str] = None,
        text: Optional[str] = None,
        message_id: Optional[str] = None,
        *,
        attachments: Optional[list[Attachment]] = None,
    ) -> str:
        """Build prompt, run AgentRuntime, extract output text.

        Two calling conventions:
          - New: ``_build_and_run_agent(cred, message=parsed, sender_name=...)``
            from the production worker pipeline.
          - Legacy: ``_build_and_run_agent(cred, event=..., chat_id=...,
            sender_id=..., sender_name=..., text=..., message_id=...)``
            from existing tests.

        Lark-specific override: error replies use ``format_lark_error_reply``
        and the friendly message is delivered back to the chat via lark_cli.
        """
        from xyz_agent_context.schema.channel_tag import ChannelTag

        # Bridge legacy kwargs → ParsedMessage if needed.
        if message is None:
            normalized_event = dict(event or {})
            normalized_event.setdefault("chat_id", chat_id or "")
            normalized_event.setdefault("sender_id", sender_id or "")
            normalized_event.setdefault("sender_name", sender_name or "Unknown")
            normalized_event.setdefault("content", text or "")
            normalized_event.setdefault("message_id", message_id or "")
            try:
                ts_ms = int(normalized_event.get("create_time", "0") or 0)
            except (ValueError, TypeError):
                ts_ms = 0
            message = ParsedMessage(
                message_id=message_id or "",
                chat_id=chat_id or "",
                sender_id=sender_id or "",
                sender_name=sender_name or "Unknown",
                content=(text or "").strip(),
                timestamp_ms=ts_ms,
                raw=normalized_event,
            )
        if sender_name is None:
            sender_name = message.sender_name

        agent_id = cred.agent_id

        builder = self.create_context_builder(message, cred, agent_id)
        prompt = await builder.build_prompt(self._history_config)

        channel_tag = ChannelTag.lark(
            sender_name=sender_name,
            sender_id=message.sender_id,
            chat_id=message.chat_id,
            chat_name=message.raw.get("chat_name", ""),
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"

        # Resolve the AGENT'S OWNER (NarraNexus user_id) — NOT the Lark
        # sender's open_id. sender_id is a Lark-internal identifier that
        # ProviderResolver can't map to an API key.
        owner_user_id = await self._resolve_agent_owner(agent_id) or agent_id

        runtime = AgentRuntime()
        # Clean retrieval anchor for narrative routing. Graceful: the anchor is
        # a retrieval optimization, so its failure must never break the agent run
        # (narrative falls back to input_content). See 2026-06-01 design.
        try:
            _anchor = await builder.build_retrieval_anchor()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"build_retrieval_anchor failed; using input fallback: {e}")
            _anchor = None
        trigger_extra_data: dict[str, Any] = {
            "channel_tag": channel_tag.to_dict(),
            "retrieval_anchor": _anchor,
            "trigger_id": (
                f"lark_{message.message_id}"
                if message.message_id
                else "lark_unknown"
            ),
        }
        # Phase 1c T9d: only attach when non-empty — matches base/Slack/WS
        # patterns so ChatModule's ``ctx_data.extra_data.get("attachments")``
        # check behaves identically across all upload sources.
        if attachments:
            trigger_extra_data["attachments"] = [
                a.model_dump(mode="json") for a in attachments
            ]
        result = await collect_run(
            runtime,
            agent_id=agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=WorkingSource.LARK,
            trigger_extra_data=trigger_extra_data,
        )

        if result.is_error:
            friendly = format_lark_error_reply(result.error)
            logger.warning(
                f"LarkTrigger [{cred.profile_name}] runtime error "
                f"({result.error.error_type}): {result.error.error_message}"
            )
            try:
                await self._cli.send_message(
                    cred.agent_id, chat_id=message.chat_id, text=friendly
                )
            except Exception as send_err:
                logger.warning(
                    f"LarkTrigger [{cred.profile_name}] failed to deliver "
                    f"error reply to Lark: {send_err}"
                )
            return friendly

        # Happy path: scrape the text the agent itself sent via
        # `lark_cli im +messages-send` from the tool_call raw payloads.
        return self.extract_output(result, message, cred)

    # ────────────────────────────────────────────────────────────────────
    # extract_output / format_error_reply overrides
    # ────────────────────────────────────────────────────────────────────

    def extract_output(self, result, message: ParsedMessage, credential) -> str:
        """Lark scrapes ``lark_cli`` tool-call args because the agent
        doesn't emit text directly — it tells the lark_cli tool to send."""
        lark_replies: list[str] = []
        for raw in result.raw_items:
            if isinstance(raw, dict):
                item = raw.get("item", {})
                if item.get("type") == "tool_call_item":
                    sent_text = self._extract_lark_reply(item)
                    if sent_text:
                        lark_replies.append(sent_text)

        if lark_replies:
            output_text = "\n".join(lark_replies)
        elif result.output_text and result.output_text.strip():
            # Agent produced reasoning text but never called lark_cli to
            # actually send a message — Communication Protocol decided
            # to stay silent (e.g. user just sent "thanks" / "got it").
            # Inbox shows this explicitly so it's clear the bot didn't
            # reply, instead of the misleading "(Replied on Lark)" stub
            # that earlier revisions wrote here.
            output_text = "(stayed silent)"
        else:
            output_text = ""

        logger.info(
            f"LarkTrigger [{credential.profile_name}] agent responded: "
            f"{output_text[:200]}"
        )
        return output_text

    def format_error_reply(self, error: RunError) -> str:
        """IM-friendly error message — see ``format_lark_error_reply``."""
        return format_lark_error_reply(error)

    @staticmethod
    def _extract_lark_reply(item: dict) -> str:
        """Extract sent text from a ``lark_cli`` tool call item.

        Expects ``tool_name="lark_cli"`` with command containing
        ``+messages-send`` or ``+messages-reply``. Returns the value of
        ``--text`` or ``--markdown``.
        """
        tool_name = item.get("tool_name", "")
        args = item.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            return ""

        if "lark_cli" not in tool_name:
            return ""

        command = args.get("command", "")
        if "+messages-send" not in command and "+messages-reply" not in command:
            return ""

        import shlex
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        for i, part in enumerate(parts):
            if part in ("--text", "--markdown") and i + 1 < len(parts):
                return _unescape_literal_escapes(parts[i + 1])
        return "(sent via lark_cli)"

    # ────────────────────────────────────────────────────────────────────
    # Inbox writer shim — for tests that call _write_to_inbox directly.
    # Production path uses self._inbox_writer.write(...) from _process_message.
    # ────────────────────────────────────────────────────────────────────

    async def _write_to_inbox(
        self,
        cred: LarkCredential,
        sender_name: str,
        sender_id: str,
        original_message: str,
        agent_response: str,
        chat_id: str,
    ) -> None:
        """Write Lark messages to MessageBus tables for Inbox display.

        Backward-compat shim: existing tests call this directly. Resolves
        ``db`` via ``get_db_client()`` so tests can monkey-patch the
        factory. Production path uses the inherited
        ``ChannelInboxWriter`` directly.
        """
        try:
            db = await get_db_client()
            # Use the channel-specific brand for the channel name in inbox.
            brand_display = "Lark" if cred.brand == "lark" else "Feishu"
            # Build a temporary writer with brand-specific display so
            # bus_channels.name reads "Feishu: Alice" or "Lark: Alice".
            from xyz_agent_context.channel.channel_inbox_writer import (
                ChannelInboxWriter,
            )
            writer = ChannelInboxWriter(self.channel_name, brand_display)
            await writer.write(
                db=db,
                agent_id=cred.agent_id,
                sender_id=sender_id,
                sender_name=sender_name,
                original_message=original_message,
                agent_response=agent_response,
                chat_id=chat_id,
            )
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")
            await self._audit(
                EVENT_INBOX_WRITE_FAILED,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={
                    "error": f"{type(e).__name__}: {e}",
                    "sender_name": sender_name,
                    "original_message": original_message[:500],
                    "agent_response": (agent_response or "")[:500],
                },
            )

    # ────────────────────────────────────────────────────────────────────
    # Backward-compat property shims so tests can:
    #   t._seen_repo = LarkSeenMessageRepository(db)
    #   t._audit_repo = LarkTriggerAuditRepository(db)
    #   t._seen_messages, t._seen_lock
    # ────────────────────────────────────────────────────────────────────

    @property
    def _seen_repo(self):
        return self._dedup_store._repo if self._dedup_store else None

    @_seen_repo.setter
    def _seen_repo(self, value):
        if self._dedup_store is None:
            self._dedup_store = ChannelDedupStore(
                channel=self.channel_name,
                repo=value,
                ttl_seconds=self.DEDUP_TTL_SECONDS,
                history_buffer_ms=self.HISTORY_BUFFER_MS,
            )
        else:
            self._dedup_store._repo = value

    @property
    def _seen_messages(self) -> dict:
        if self._dedup_store is None:
            return {}
        return self._dedup_store._memory_cache

    @_seen_messages.setter
    def _seen_messages(self, value: dict):
        # Some tests pre-populate the memory cache. Build a transient store
        # if needed.
        if self._dedup_store is None:
            self._dedup_store = ChannelDedupStore(
                channel=self.channel_name,
                repo=None,
                ttl_seconds=self.DEDUP_TTL_SECONDS,
                history_buffer_ms=self.HISTORY_BUFFER_MS,
            )
        self._dedup_store._memory_cache = value

    # Backward-compat alias for tests that reference _sanitize_display_name.
    # Base class renamed to sanitize_display_name; both stay so existing
    # tests keep passing.
    _sanitize_display_name = ChannelTriggerBase.sanitize_display_name

    @property
    def _seen_lock(self) -> threading.Lock:
        if self._dedup_store is None:
            # Build a transient store so the test's `with t._seen_lock:`
            # block works.
            self._dedup_store = ChannelDedupStore(
                channel=self.channel_name,
                repo=None,
                ttl_seconds=self.DEDUP_TTL_SECONDS,
                history_buffer_ms=self.HISTORY_BUFFER_MS,
            )
        return self._dedup_store._memory_lock
