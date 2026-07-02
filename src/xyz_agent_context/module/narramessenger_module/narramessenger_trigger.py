"""
@file_name: narramessenger_trigger.py
@date: 2026-06-17
@description: NarraMessenger channel trigger built on ``ChannelTriggerBase``.

Transport: **Gateway Polling** (HTTP long-poll), structurally identical to
TelegramTrigger's ``getUpdates`` loop. ``connect()`` activates the gateway
(``POST /connect``) once, then long-polls ``GET /invocations/poll`` and yields
each invocation dict. The shared dedup → worker pipeline owned by the base
class handles everything downstream.

Why gateway poll (not a Matrix client): the platform pre-filters DM/@mention
AND authorizes before handing us an invocation, so we need NO per-event
authorize-event step (the base pipeline is used unchanged). Replies go out via
the ``narra_send`` MCP tool → ``/chat/send`` (no 15-min deadline), so this
trigger only RECEIVES.

NarraMessenger-specific notes:
  - **Text-only for the agent**: non-text (images/files/audio) arrive as
    ``[Image]`` / ``[File: x]`` placeholders inside ``message`` — there is no
    attachment payload, so ``fetch_attachments`` is NOT overridden.
  - **No reply via the trigger**: ``extract_output`` scrapes the agent's
    ``narra_send`` tool-call args for the inbox record (NOT ``output_text`` —
    same chain-of-thought-leak guard as Telegram/Slack).
  - **update_guide_required** poll payloads are acked programmatically; we do
    NOT execute the runtime self-update document.
  - **Owner auto-claim**: ``do_bind`` never learns the binder's Matrix
    identity (only the agent's own), so ``_process_message`` claims the
    first sender in the bind room (``credential.bind_room_id``) as owner —
    see ``_maybe_claim_owner`` below.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage

from ._narramessenger_client import (
    NarramessengerAPIError,
    NarramessengerClient,
    is_permanent_api_error,
)
from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)
from .narramessenger_context_builder import NarramessengerContextBuilder


class NarramessengerTrigger(ChannelTriggerBase):
    """NarraMessenger channel trigger (Gateway Polling)."""

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "narramessenger"
    brand_display = "NarraMessenger"
    working_source = WorkingSource.NARRAMESSENGER

    # ── Worker pool ──────────────────────────────────────────────────────
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # NarraMessenger pushes one invocation per real message; no client-side
    # debounce needed (the platform already coalesces).
    DEBOUNCE_WINDOW_MS = 0

    # Long-poll tuning — server blocks up to 30s.
    POLL_TIMEOUT_MS = 30000
    POLL_IDLE_SLEEP_SECONDS = 0.2

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            # History is carried INLINE in each invocation (``context`` /
            # ``group_context.history_messages``) — the context builder reads
            # it from ParsedMessage.raw, so the base's history loader is left
            # off (no separate per-room history fetch).
            history_config=ChannelHistoryConfig(
                load_conversation_history=False,
                history_limit=20,
                history_max_chars=20000,
            ),
        )
        # Per-credential client kept so stop() can close cleanly.
        self._clients: dict[str, NarramessengerClient] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        await super().start(db)
        logger.info(
            f"NarramessengerTrigger started: {len(self._workers)} workers, "
            f"watching channel_narramessenger_credentials for active rows"
        )

    async def stop(self) -> None:
        for key, client in list(self._clients.items()):
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[narramessenger:{key}] client close during stop: {e}")
        self._clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(self) -> list[NarramessengerCredential]:
        if not self._db:
            return []
        mgr = NarramessengerCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(self, credential: NarramessengerCredential) -> str:  # type: ignore[override]
        return credential.agent_id

    def is_permanent_auth_failure(self, exc: BaseException) -> bool:  # type: ignore[override]
        return is_permanent_api_error(exc)

    async def disable_credential(self, credential: NarramessengerCredential) -> None:  # type: ignore[override]
        if not self._db:
            return
        mgr = NarramessengerCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    async def connect(
        self, credential: NarramessengerCredential
    ) -> AsyncIterator[dict]:
        """Activate the gateway, then long-poll and yield invocation dicts.

        ``no_invocation`` → idle wake-up. ``update_guide_required`` → ack the
        version programmatically and keep polling. A real invocation (has
        ``invocation_id``) is yielded into the base pipeline. Permanent
        errors (401/409) propagate so the base watcher disables the
        credential; transient errors propagate for backoff + reconnect.
        """
        client = NarramessengerClient(credential.bearer_token, credential.backend_base_url)
        key = self._subscriber_key(credential)
        self._clients[key] = client

        logger.info(
            f"[narramessenger:{credential.agent_id}] connecting gateway "
            f"(base={credential.backend_base_url})"
        )

        try:
            # Activate transport once. 409 here is permanent (re-bind needed).
            await client.connect()

            while self.running:
                resp = await client.poll(timeout_ms=self.POLL_TIMEOUT_MS)
                status = resp.get("status")

                if status == "no_invocation":
                    await asyncio.sleep(self.POLL_IDLE_SLEEP_SECONDS)
                    continue

                if status == "update_guide_required":
                    version = resp.get("target_version")
                    if version is not None:
                        try:
                            await client.ack_update_guide(int(version))
                            logger.info(
                                f"[narramessenger:{credential.agent_id}] acked "
                                f"update-guide version {version} (pinned contract)"
                            )
                        except NarramessengerAPIError as e:
                            logger.warning(
                                f"[narramessenger:{credential.agent_id}] "
                                f"update-guide ack failed: {e.code}"
                            )
                    continue

                if resp.get("invocation_id"):
                    yield resp
                else:
                    # Unknown shape — log once and keep going.
                    logger.warning(
                        f"[narramessenger:{credential.agent_id}] unexpected poll "
                        f"payload keys={list(resp.keys())}"
                    )
                    await asyncio.sleep(self.POLL_IDLE_SLEEP_SECONDS)
        finally:
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._clients.pop(key, None)

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """NarraMessenger invocation → ParsedMessage. None means "skip".

        The full invocation is stashed in ``raw`` so the context builder can
        read ``context`` / ``group_context`` without re-fetching. Non-text
        already arrives flattened into ``message`` as ``[Image]`` etc.
        """
        invocation_id = raw.get("invocation_id", "")
        if not invocation_id:
            return None

        room_id = raw.get("room_id") or raw.get("conversation_id") or ""
        if not room_id:
            return None

        sender = raw.get("sender") or {}
        sender_id = sender.get("matrix_user_id", "") or ""
        sender_name = sender.get("display_name", "") or sender_id

        content = raw.get("message", "") or ""
        is_group = bool(raw.get("is_group_chat"))

        # DM payloads carry no timestamp; group ones expose
        # group_context.trigger_message.origin_server_ts (ms).
        timestamp_ms = 0
        gc = raw.get("group_context") or {}
        trig = gc.get("trigger_message") or {}
        try:
            timestamp_ms = int(trig.get("origin_server_ts", 0) or 0)
        except (ValueError, TypeError):
            timestamp_ms = 0

        # Dedup keys on (channel, message_id). Each invocation is popped from
        # the queue exactly once; the group trigger event_id is more stable
        # than the invocation_id when present, so prefer it.
        message_id = trig.get("event_id") or invocation_id

        if not content.strip():
            return None

        return ParsedMessage(
            message_id=str(message_id),
            chat_id=str(room_id),
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            chat_type=ChatType.GROUP if is_group else ChatType.PRIVATE,
            timestamp_ms=timestamp_ms,
            raw=raw,
        )

    async def is_echo(
        self, message: ParsedMessage, credential: NarramessengerCredential
    ) -> bool:
        """True when the message was authored by our own agent identity.

        Gateway invocations are only generated for inbound user messages, so
        this is a belt-and-suspenders guard against the platform ever echoing
        the agent's own ``/chat/send`` output back as an invocation.
        """
        if not credential.matrix_user_id:
            return False
        return message.sender_id == credential.matrix_user_id

    async def resolve_sender_name(
        self, sender_id: str, credential: NarramessengerCredential
    ) -> str:
        """The invocation already carries ``sender.display_name`` (extracted by
        parse_event). This fallback (used only when the parsed name is empty)
        returns the raw id rather than burning an API call."""
        return sender_id

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        return NarramessengerContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
        )

    # ────────────────────────────────────────────────────────────────────
    # Inbound preprocessing — owner auto-claim
    # ────────────────────────────────────────────────────────────────────

    async def _process_message(
        self, credential: NarramessengerCredential, message: ParsedMessage
    ) -> None:
        """Override to auto-claim the owner identity before invoking AgentRuntime.

        WHY this lives here (not in ``do_bind``):
            Unlike Telegram, NarraMessenger's bind flow never surfaces the
            *binder's* identity at all. ``POST /api/agent-gateway/connect``
            (driven by ``_narramessenger_service.do_bind``) returns the
            AGENT's own ``matrixUserId``/``principalId``/``roomId`` — there
            is no equivalent of Telegram's ``owner_username`` lock to carry
            forward from bind time. The one thing bind DOES capture is the
            room the bind happened in (``bind_room_id``, from the connect
            response's ``roomId``), so the first inbound DM in that exact
            room is the only signal we have.

        SECURITY MODEL:
            The bind room is a 1:1 Matrix DM the platform creates for the
            person running the bind flow — only they and the agent are
            members. We claim ownership only when an inbound message's
            ``chat_id`` equals ``credential.bind_room_id`` *exactly*; a
            message from any other room (a different DM, or a group the
            agent is later added to) never triggers a claim. This is NOT
            "first message anywhere wins" — it is "first sender in the
            room the owner themselves created wins."

            The claim is attempted BEFORE ``super()._process_message``
            (which owns the ``is_echo`` filter), so ``_should_claim_owner``
            independently excludes ``message.sender_id ==
            credential.matrix_user_id`` — i.e. the agent's own identity.
            Without this, a platform that ever echoes the agent's own
            outbound ``/chat/send`` back into the bind room as an
            invocation would let the agent permanently claim itself as its
            own owner (the claim is idempotent — it never fires again once
            set). We deliberately duplicate the self-sender check here
            rather than reordering to run after ``is_echo``: the claim
            gate must be safe standing alone, since ``is_echo`` is base-
            class behaviour this subclass doesn't control the timing of.

        Idempotent: only fires while ``owner_matrix_user_id`` is still
        empty. Once claimed it is persisted and never re-evaluated.
        """
        await self._maybe_claim_owner(credential, message)
        return await super()._process_message(credential, message)

    @staticmethod
    def _should_claim_owner(
        credential: NarramessengerCredential, message: ParsedMessage
    ) -> bool:
        """The claim gate — see ``_process_message`` docstring for the
        security model. Split out as a pure function so it can be tested
        without exercising the full agent pipeline.

        ``message.sender_id != credential.matrix_user_id`` guards against
        the agent claiming itself as owner from an echoed message (see
        ``_process_message`` docstring) — this check is independent of,
        and can't rely on, the base class's ``is_echo`` filter, since the
        claim is attempted before that filter runs.
        """
        return bool(
            not credential.owner_matrix_user_id
            and credential.bind_room_id
            and message.chat_id == credential.bind_room_id
            and message.sender_id
            and message.sender_id != credential.matrix_user_id
        )

    async def _maybe_claim_owner(
        self, credential: NarramessengerCredential, message: ParsedMessage
    ) -> None:
        """Claim the sender of the first bind-room message as the owner.

        Writes ``owner_matrix_user_id``/``owner_name`` to DB and mutates
        the in-memory ``credential`` so THIS turn's ``build_extra_data``
        (which re-fetches the credential from DB) already sees the claim —
        same pattern as ``TelegramTrigger._maybe_resolve_owner``.
        """
        if not self._should_claim_owner(credential, message) or not self._db:
            return
        owner_name = message.sender_name or message.sender_id
        mgr = NarramessengerCredentialManager(self._db)
        await mgr.update_owner(credential.agent_id, message.sender_id, owner_name)
        credential.owner_matrix_user_id = message.sender_id
        credential.owner_name = owner_name
        logger.info(
            f"[narramessenger:{credential.agent_id}] auto-claimed owner "
            f"{message.sender_id} from first bind-room message"
        )

    # ────────────────────────────────────────────────────────────────────
    # Reply-side overrides
    # ────────────────────────────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: NarramessengerCredential
    ) -> str:
        """Pull reply text from the agent's ``narra_reply`` / ``narra_send`` calls.

        Do NOT use ``result.output_text`` — it can contain the agent's
        reasoning and would leak chain-of-thought into the inbox (Phase 3
        Slack regression). Scrape the ``text`` arg from the reply tool calls
        (``narra_reply`` for the in-turn reply, ``narra_send`` for a proactive
        send the agent chose to make this turn).
        """
        replies: list[str] = []
        for raw in getattr(result, "raw_items", []) or []:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent_text = self._extract_narra_reply(item)
            if sent_text:
                replies.append(sent_text)

        output_text = "\n".join(replies) if replies else "(stayed silent)"
        logger.info(
            f"NarramessengerTrigger [{credential.agent_id}] agent responded: "
            f"{output_text[:200]}"
        )
        return output_text

    @staticmethod
    def _extract_narra_reply(item: dict) -> str:
        """Extract sent text from a ``narra_reply`` / ``narra_send`` tool call."""
        tool_name = item.get("tool_name", "")
        if "narra_reply" not in tool_name and "narra_send" not in tool_name:
            return ""

        raw_args = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""

        text = raw_args.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def format_error_reply(self, error) -> str:
        return (
            f"Sorry, I hit an error processing your message: "
            f"{getattr(error, 'message', '') or 'unknown'}"
        )
