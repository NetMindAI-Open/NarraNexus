"""
@file_name: message_bus_trigger.py
@author: NarraNexus
@date: 2026-04-03
@description: Background poller that delivers pending messages to agents

Polls bus_messages table, triggers AgentRuntime for agents with
unprocessed messages.

Design:
- Single poller cycles through all registered agents (from bus_agent_registry)
- Groups pending messages by channel_id (per-channel batching)
- For each channel with pending messages, triggers AgentRuntime.run()
- On success: advances the cursor via ack_processed()
- On failure: records failure via record_failure()

Usage:
    DATABASE_URL=sqlite:///path/to/db uv run python -m xyz_agent_context.message_bus.message_bus_trigger
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import Dict, List

from loguru import logger

from xyz_agent_context.agent_framework.llm_failure import (
    MAX_REDACTED_ERROR_LEN,
    is_credential_error,
    redact_secrets,
)
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.schemas import BusMessage

# Poll interval in seconds (initial; adaptive bounds below)
POLL_INTERVAL = 3

# A team group-chat channel's ``created_by`` is this prefix + team_id (a
# non-agent marker), set by the team-chat route. It both identifies the
# room and ensures no member agent is the always-activated channel owner —
# delivery is purely @-mention driven. Keep in sync with backend/routes/teams.py.
TEAM_ROOM_OWNER_PREFIX = "team_"

# The user posts into a team room as this prefix + user_id (a non-agent
# sender). Keep in sync with backend/routes/teams.py.
USER_SENDER_PREFIX = "usr_"

# Maximum concurrent agent processing workers
MAX_WORKERS = 3

# Rate limiting constants
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 1800  # 30 minutes in seconds

# Adaptive polling constants. Kept low so a team group-chat reply lands quickly
# (the trigger is a separate process; this is the latency the user feels after
# an idle period). Worst-case idle latency ≈ POLL_MAX_INTERVAL.
POLL_MIN_INTERVAL = 3
POLL_MAX_INTERVAL = 12
POLL_STEP_UP = 3

# Team group chat: cap how many consecutive agent-to-agent hops can keep the
# @-mention cascade alive without a human message. Past this, an agent reply's
# @mentions are dropped so two agents can't @ each other forever. A user
# message resets the chain.
MAX_TEAM_AGENT_HOPS = 4

# Kept in sync with LocalMessageBus.get_pending_messages' inline
# `failure_count < 3` poison-message filter (local_bus.py). Once a message's
# failure_count reaches this, it is permanently dropped from the pending
# queue with no further retries — see `_notify_permanent_failure` below,
# which is the only signal the owner gets when that happens.
POISON_FAILURE_THRESHOLD = 3

# De-dup window for permanent-failure inbox notices, keyed per
# (agent_id, error_category). Same window as the rate limiter — a batch of
# messages failing for one root cause (e.g. a broken provider key) should
# not write one inbox row per message.
FAILURE_NOTIFY_COOLDOWN_SECONDS = 1800  # 30 minutes

# Credential-error classification and secret redaction moved to the shared
# ``agent_framework.llm_failure`` module so every background LLM path (bus,
# narrative updater, Step-5 hooks) asks the same questions the same way.
# ``_classify_error`` / ``_redact_error_for_owner`` below delegate to it.
MAX_NOTIFIED_ERROR_LEN = MAX_REDACTED_ERROR_LEN


def im_channel_prefixes() -> tuple[str, ...]:
    """Channel-id prefixes owned by dedicated IM triggers — registry-driven.

    ChannelInboxWriter persists IM turns to ``bus_messages`` under
    ``{channel}_{chat_id}`` purely for history/Inbox display; the channel's
    own trigger already ran AgentRuntime for them. Those rows must never be
    re-dispatched here. The set used to be a hand-maintained tuple
    ("lark_", "telegram_", "slack_") that silently drifted — wechat,
    narramessenger and discord were missing, so every message on those
    channels fired a SECOND agent run wearing the Owner-Relay peer-agent
    prompt (2026-07-03 wechat incident: fabricated context_token sends +
    bogus "我已经在微信上回复你啦" platform DMs). Deriving from
    ``MessageSourceHandler.dedicated_trigger`` keeps a future channel
    covered the moment it registers; computed per call because channel
    modules register at import time and import order isn't guaranteed.
    """
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )

    return tuple(sorted(
        f"{name}_"
        for name, handler in MessageSourceRegistry.handlers().items()
        if handler.dedicated_trigger
    ))


def build_bus_anchor(messages: List[BusMessage]) -> str:
    """Build the clean retrieval anchor for a bus turn.

    The execution prompt (_build_prompt) wraps peer messages in a per-turn
    ~1217-char Owner-Relay boilerplate + From/Time metadata — bus was the only
    real 400 source in prod. The anchor keeps ONLY each peer's body (tagged
    with the sender agent), so the narrative query vector is clean. Oversized
    backlogs are still capped downstream by a length guard.
    See the 2026-06-01 design doc.
    """
    return "\n".join(
        f"[From agent {m.from_agent}] {m.content}" for m in messages
    )


class MessageBusTrigger:
    """
    Background poller that processes pending MessageBus messages.

    Cycles through all registered agents, finds unprocessed messages,
    and triggers AgentRuntime to handle them.

    Args:
        bus: A MessageBusService instance (typically LocalMessageBus).
        poll_interval: Seconds between poll cycles.
        max_workers: Maximum concurrent agent processing tasks.
    """

    def __init__(
        self,
        bus: LocalMessageBus,
        poll_interval: int = POLL_INTERVAL,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._running = False
        self._semaphore = asyncio.Semaphore(max_workers)
        self._rate_counters: Dict[str, List[float]] = {}
        self._current_interval = poll_interval
        # Per-agent serialisation lock. The global ``_semaphore`` caps
        # concurrent agents but does NOT prevent the same agent from
        # being processed twice in parallel — `get_pending_messages`
        # only filters on ``last_processed_at``, which is advanced
        # after ``_invoke_runtime`` returns. AgentRuntime takes minutes
        # for an LLM-heavy turn; the poll loop fires every 10s; without
        # this lock the same bus_message gets handed to AgentRuntime
        # 3+ times. Observed in production (2026-05-12 13:20 — agent
        # processed one msg_4eb528dc three times, burned ~30K tokens).
        self._agent_locks: Dict[str, asyncio.Lock] = {}
        # last `time.monotonic()` a permanent-failure inbox notice was
        # written for a given "agent_id:error_category" key. See
        # `_notify_permanent_failure`.
        self._failure_notify_cooldown: Dict[str, float] = {}

    async def start(self) -> None:
        """Start the polling loop with adaptive interval."""
        self._running = True
        logger.info(
            f"MessageBusTrigger started (poll_interval={self._poll_interval}s, "
            f"max_workers={self._max_workers})"
        )

        while self._running:
            try:
                had_messages = await self._poll_cycle()
                if had_messages:
                    self._current_interval = POLL_MIN_INTERVAL
                else:
                    self._current_interval = min(
                        self._current_interval + POLL_STEP_UP,
                        POLL_MAX_INTERVAL,
                    )
            except Exception as e:
                logger.exception(f"MessageBusTrigger poll cycle error: {e}")

            await asyncio.sleep(self._current_interval)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False
        logger.info("MessageBusTrigger stopping")

    async def _poll_cycle(self) -> bool:
        """Run one poll cycle. Returns True if any messages were found."""
        # Get all agents that are members of any channel (not just registered ones)
        rows = await self._bus._db.execute(
            "SELECT DISTINCT agent_id FROM bus_channel_members", ()
        )
        agent_ids = [r["agent_id"] for r in rows] if rows else []
        if not agent_ids:
            return False

        had_messages = False
        tasks = []
        for aid in agent_ids:
            tasks.append(self._process_agent(aid))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if r is True:
                had_messages = True

        return had_messages

    def _should_process_message(
        self, msg: BusMessage, agent_id: str, channel_type: str, channel_owner: str,
    ) -> bool:
        """Check if a message should trigger processing for an agent.

        Rules:
        - Never process own messages
        - DM (direct) channels: always process
        - Group channels:
            * Channel owner (created_by) is ALWAYS activated by any new message
            * Other members: only process if mentioned or @everyone
        """
        if msg.from_agent == agent_id:
            return False
        if channel_type == "direct":
            return True
        # Channel owner is always activated, regardless of mentions
        if agent_id == channel_owner:
            return True
        if not msg.mentions:
            return False
        return agent_id in msg.mentions or "@everyone" in msg.mentions

    async def _get_channel_info(self, channel_id: str) -> tuple[str, str]:
        """Get (channel_type, created_by) for a channel."""
        # get_one builds dialect-correct SQL per backend. ``self._bus._db`` is
        # the RAW backend (LocalMessageBus is handed db._backend, not the
        # AsyncDatabaseClient wrapper), so the raw ``execute`` path takes the
        # query verbatim with NO %s→? translation — a MySQL `%s` placeholder
        # threw `near "%"` on SQLite and silently broke bus delivery for every
        # agent that had channel messages (2026-06-09: 影/镜 never received 零's
        # messages). get_one sidesteps the placeholder problem entirely.
        row = await self._bus._db.get_one("bus_channels", {"channel_id": channel_id})
        if row:
            return (
                row.get("channel_type", "group"),
                row.get("created_by", ""),
            )
        return ("group", "")

    def _check_rate_limit(self, agent_id: str, channel_id: str) -> bool:
        """Return True if within rate limit, False if exceeded."""
        key = f"{agent_id}:{channel_id}"
        now = time.monotonic()
        timestamps = self._rate_counters.get(key, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_MAX:
            logger.warning(
                f"Rate limit exceeded for {agent_id} in channel {channel_id} "
                f"({len(timestamps)}/{RATE_LIMIT_MAX} in {RATE_LIMIT_WINDOW}s)"
            )
            return False
        timestamps.append(now)
        self._rate_counters[key] = timestamps
        return True

    async def _process_agent(self, agent_id: str) -> bool:
        """Process pending messages for an agent. Returns True if messages handled.

        Acquires a per-agent lock so a slow ``_invoke_runtime`` does not let
        the next poll fire a second AgentRuntime for the same pending
        message. See ``__init__`` for the production incident this guards.
        """
        # Circuit-breaker skip-gate: a paused (dead key / quota) or cooling
        # agent is skipped entirely — its pending messages are left queued
        # (NOT acked), so they are handled once it resumes. This is what
        # frees the bus from re-triggering a broken agent every poll. Checked
        # before the semaphore so a paused agent doesn't hold a slot.
        #
        # Accepted trade-off: while an agent stays paused (owner hasn't fixed
        # the key yet), its channel backlog accumulates and is drained in one
        # burst on resume. That's intended — dropping/ack'ing messages for a
        # temporarily-broken agent would be silent data loss; the backlog
        # converges once the owner reconfigures and the breaker re-arms.
        from xyz_agent_context.agent_framework.agent_circuit_breaker import should_skip
        cb_skip, cb_reason = await should_skip(agent_id)
        if cb_skip:
            logger.debug(
                f"MessageBusTrigger: skipping agent {agent_id} "
                f"(circuit-breaker: {cb_reason})"
            )
            return False

        lock = self._agent_locks.setdefault(agent_id, asyncio.Lock())
        async with lock, self._semaphore:
            try:
                pending = await self._bus.get_pending_messages(agent_id)
                if not pending:
                    return False

                by_channel: Dict[str, List[BusMessage]] = defaultdict(list)
                for msg in pending:
                    by_channel[msg.channel_id].append(msg)

                handled_any = False
                for channel_id, messages in by_channel.items():
                    # Skip IM-channel-owned channels — each has its own dedicated
                    # trigger that already processed the message; re-consuming
                    # would fire AgentRuntime a second time and send duplicate
                    # replies. Prefixes derive from MessageSourceRegistry (see
                    # im_channel_prefixes) so new channels can't be forgotten.
                    if channel_id.startswith(im_channel_prefixes()):
                        latest = max(messages, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(agent_id, channel_id, latest.created_at)
                        continue

                    channel_type, channel_owner = await self._get_channel_info(channel_id)

                    # Mention filtering (channel owner is always activated)
                    relevant = [
                        m for m in messages
                        if self._should_process_message(m, agent_id, channel_type, channel_owner)
                    ]
                    if not relevant:
                        # Still ack to advance cursor
                        latest = max(messages, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(
                            agent_id, channel_id, latest.created_at
                        )
                        continue

                    # Rate limiting
                    if not self._check_rate_limit(agent_id, channel_id):
                        latest = max(relevant, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(
                            agent_id, channel_id, latest.created_at
                        )
                        continue

                    trigger_msg = relevant[-1]
                    await self._handle_channel_batch(
                        agent_id, channel_id, relevant, trigger_msg, channel_owner
                    )
                    handled_any = True

                return handled_any
            except Exception as e:
                logger.exception(
                    f"MessageBusTrigger: error processing agent {agent_id}: {e}"
                )
                return False

    async def _get_agent_owner(self, agent_id: str) -> str:
        """Look up the owner user_id for an agent. Returns "" if unknown."""
        try:
            from xyz_agent_context.utils.db_factory import get_db_client
            db = await get_db_client()
            row = await db.get_one("agents", {"agent_id": agent_id})
            return (row or {}).get("created_by", "") or ""
        except Exception as e:
            logger.warning(f"_get_agent_owner({agent_id}) failed: {e}")
            return ""

    async def _handle_channel_batch(
        self,
        agent_id: str,
        channel_id: str,
        messages: List[BusMessage],
        trigger_message: BusMessage,
        channel_owner: str = "",
    ) -> None:
        """
        Handle a batch of messages from a single channel for an agent.

        Builds a prompt, invokes AgentRuntime, and on success advances the
        processing cursor. On failure, records the failure for retry tracking.

        Team group chat (``channel_owner`` is the synthetic ``team_<id>``
        marker) is a distinct surface: the agent gets a group-chat prompt
        (not the owner-relay), and its reply is posted BACK INTO the channel
        — with any @mentions parsed so teammates get pulled in — so the user
        and teammates all see it in the shared room. Every other channel
        (peer DM, IM bridges) keeps the original owner-relay + inbox path.
        """
        is_team = channel_owner.startswith(TEAM_ROOM_OWNER_PREFIX)
        member_map: Dict[str, str] = {}
        try:
            if is_team:
                member_map = await self._team_member_names(channel_id)
                prompt = self._build_team_prompt(agent_id, messages, member_map)
            else:
                # Owner lookup up-front — used by both the prompt (to remind the
                # agent its owner is waiting in chat) and the inbox writer.
                owner_user_id = await self._get_agent_owner(agent_id)
                # Resolve the owner's human name for the relay prose (the raw
                # user_id stays as the send_message_to_user_directly routing key).
                owner_name = ""
                if owner_user_id:
                    from xyz_agent_context.utils.db_factory import get_db_client
                    from xyz_agent_context.repository import UserRepository
                    owner_name = await UserRepository(await get_db_client()).get_display_name(owner_user_id)

                # Build prompt from messages
                prompt = self._build_prompt(
                    messages, owner_user_id=owner_user_id, owner_name=owner_name
                )

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages, team={is_team})"
            )

            # Call AgentRuntime. Pass a clean retrieval anchor (peer bodies
            # only, no Owner-Relay boilerplate) for narrative routing — the
            # execution `prompt` is far noisier. See 2026-06-01 design.
            response_text = await self._invoke_runtime(
                agent_id=agent_id,
                sender_agent_id=trigger_message.from_agent,
                prompt=prompt,
                channel_id=channel_id,
                trigger_message_id=trigger_message.message_id,
                retrieval_anchor=build_bus_anchor(messages),
            )

            # On success: advance cursor
            await self._bus.ack_processed(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=trigger_message.created_at,
            )

            logger.info(
                f"MessageBusTrigger: agent {agent_id} processed "
                f"{len(messages)} messages in channel {channel_id}"
            )

            if response_text:
                if is_team:
                    # Post the reply back into the shared room as this agent.
                    # Parse @mentions so an agent can hand off to a teammate
                    # (e.g. "@rabbit can you summarise?") and pull them in.
                    mentions = self._extract_team_mentions(response_text, member_map)
                    # Cap agent↔agent cascades: if too many agent hops have
                    # piled up since the last human message, stop propagating
                    # @mentions so two agents can't loop forever.
                    if mentions:
                        depth = await self._team_cascade_depth(channel_id)
                        if depth >= MAX_TEAM_AGENT_HOPS:
                            logger.info(
                                f"Team cascade depth {depth} >= {MAX_TEAM_AGENT_HOPS} "
                                f"in {channel_id}; dropping @mentions to break the loop"
                            )
                            mentions = []
                    await self._bus.send_message(
                        from_agent=agent_id,
                        to_channel=channel_id,
                        content=response_text,
                        mentions=mentions or None,
                    )
                else:
                    # Write response to inbox
                    await self._write_to_inbox(
                        agent_id, channel_id, trigger_message, response_text
                    )

        except Exception as e:
            logger.exception(
                f"MessageBusTrigger: failed to process channel {channel_id} "
                f"for agent {agent_id}: {e}"
            )
            # Record failure for the trigger message
            await self._bus.record_failure(
                message_id=trigger_message.message_id,
                agent_id=agent_id,
                error=str(e),
            )
            # Once this message crosses the poison threshold,
            # `get_pending_messages` will filter it out forever (local_bus.py)
            # — this is the one chance to tell the owner it happened.
            failure_count = await self._bus.get_failure_count(
                trigger_message.message_id, agent_id
            )
            if failure_count >= POISON_FAILURE_THRESHOLD:
                await self._notify_permanent_failure(
                    agent_id=agent_id,
                    channel_id=channel_id,
                    trigger_message=trigger_message,
                    error=str(e),
                )

    @staticmethod
    def _classify_error(error: str) -> str:
        """Coarse category used for (a) the cooldown de-dup key and (b) the
        hint text shown to the owner. Deliberately a substring match, not a
        parsed exception type — `record_failure` only ever gets a `str(e)`,
        the original exception is already gone by the time this runs.

        Runs on the RAW error (before `_redact_error_for_owner` masks
        anything) — classification only reads for keyword markers like
        "api_key" / "401", it never displays the raw string, so there is
        nothing to redact here.
        """
        return "provider_credential" if is_credential_error(error) else "generic"

    @staticmethod
    def _redact_error_for_owner(error: str) -> str:
        """Mask secret-looking substrings and cap the length before an
        error string is echoed into the owner-facing inbox notification.

        Provider SDKs routinely echo the offending credential back in the
        error body (OpenAI: "Incorrect API key provided: sk-..."), so
        `str(exception)` must never be written verbatim to a place the
        owner (and anyone with inbox access) can read. This is a coarse
        pattern mask, not a full secret scanner — good enough for the
        common `sk-...` / `key=...` / `Bearer ...` shapes, not a security
        boundary for arbitrary provider error formats.
        """
        return redact_secrets(error, MAX_NOTIFIED_ERROR_LEN)

    async def _notify_permanent_failure(
        self,
        agent_id: str,
        channel_id: str,
        trigger_message: BusMessage,
        error: str,
    ) -> None:
        """Surface a permanently-dropped bus message to the owner's inbox.

        Without this, hitting `POISON_FAILURE_THRESHOLD` is a pure silent
        failure (upstream: NetMindAI-Open/NarraNexus#52) — e.g. a broken
        OpenAI provider key makes every `_invoke_runtime` call raise, and
        after 3 failures the message just vanishes from
        `get_pending_messages` forever with zero owner-facing signal.

        De-duplicated per (agent_id, error category) via
        `_failure_notify_cooldown` (same in-memory, per-process pattern as
        `_rate_counters` — a process restart resets it, which is an accepted
        tradeoff here too) so a burst of messages failing for one root cause
        writes at most one inbox row per `FAILURE_NOTIFY_COOLDOWN_SECONDS`.

        The cooldown is armed ONLY after a successful inbox write (see the
        end of the `try` block) — arming it up-front would let one transient
        write failure (DB blip, etc.) silently suppress the real
        notification for the rest of the cooldown window.
        """
        category = self._classify_error(error)
        cooldown_key = f"{agent_id}:{category}"
        now = time.monotonic()
        last_notified = self._failure_notify_cooldown.get(cooldown_key)
        if (
            last_notified is not None
            and now - last_notified < FAILURE_NOTIFY_COOLDOWN_SECONDS
        ):
            return

        try:
            owner_user_id = await self._get_agent_owner(agent_id)
            if not owner_user_id:
                logger.warning(
                    f"Cannot notify of permanent bus failure: agent "
                    f"{agent_id} has no resolvable owner"
                )
                return

            import uuid

            from xyz_agent_context.repository.inbox_repository import (
                InboxRepository,
            )
            from xyz_agent_context.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )
            from xyz_agent_context.utils.db_factory import get_db_client

            if category == "provider_credential":
                hint = (
                    "This looks like a provider/credential problem — check "
                    "the agent's LLM provider configuration (API key, base "
                    "URL) in Provider settings, then retry the message."
                )
            else:
                hint = (
                    "Check the agent's recent activity for details, then "
                    "retry the message."
                )

            safe_error = self._redact_error_for_owner(error)
            content = (
                f"Your agent could not process a message on channel "
                f"{channel_id} after {POISON_FAILURE_THRESHOLD} attempts "
                f"and has stopped retrying it automatically.\n\n"
                f"Error: {safe_error}\n\n{hint}"
            )

            db = await get_db_client()
            await InboxRepository(db).create_message(
                user_id=owner_user_id,
                message_id=f"busfail_{uuid.uuid4().hex[:16]}",
                title=f"Message delivery failed: {agent_id}",
                content=content,
                message_type=InboxMessageType.SYSTEM_NOTICE,
                source=MessageSource(type="message_bus_failure", id=channel_id),
            )
            # Arm the cooldown only now that the write actually succeeded.
            self._failure_notify_cooldown[cooldown_key] = now
            logger.warning(
                f"MessageBusTrigger: notified owner {owner_user_id} of "
                f"permanent failure for agent {agent_id} in channel "
                f"{channel_id} (category={category})"
            )
        except Exception as notify_err:  # noqa: BLE001 — notification is best-effort
            logger.warning(
                f"Failed to write permanent-failure notification to inbox: "
                f"{notify_err}"
            )

    async def _team_member_names(self, channel_id: str) -> Dict[str, str]:
        """Map each channel member's agent_id → display name (agent_name)."""
        out: Dict[str, str] = {}
        for m in await self._bus.get_channel_members(channel_id):
            row = await self._bus._db.get_one("agents", {"agent_id": m.agent_id})
            if row:
                out[m.agent_id] = row.get("agent_name") or m.agent_id
        return out

    def _build_team_prompt(
        self, agent_id: str, messages: List[BusMessage], member_map: Dict[str, str]
    ) -> str:
        """Group-chat prompt for a team room. The agent's plain reply is posted
        back into the shared room (the user + teammates see it), so — unlike the
        peer/owner-relay path — there is no send_message_to_user_directly step."""
        me = member_map.get(agent_id, agent_id)
        teammates = [n for a, n in member_map.items() if a != agent_id]
        roster = ", ".join(teammates) if teammates else "(no other agents yet)"
        lines = [
            "[Team Group Chat]",
            f'You are "{me}" in a team group chat with the user and your '
            f"teammates.",
            f"Channel members RIGHT NOW (besides the user): {roster}.",
            "These are the ONLY participants who can see this chat. Someone "
            "named in the history but not in that list has LEFT or was never "
            "here — they are not present.",
            "",
            "Recent messages:",
        ]
        for msg in messages:
            sender = (
                "User"
                if msg.from_agent.startswith(USER_SENDER_PREFIX)
                else member_map.get(msg.from_agent, msg.from_agent)
            )
            lines.append(f"{sender}: {msg.content}")
        lines += [
            "",
            "Write your chat reply now. Rules:",
            "- Output ONLY the message itself — natural, conversational text "
            "(markdown is fine). It is posted to the group as-is; everyone sees it.",
            "- Do NOT use any tools and do NOT call any send/bus function. Your "
            "text reply is delivered automatically — there is nothing to invoke.",
            "- Do NOT narrate your process or thinking. No \"Let me…\", no \"I "
            "need to find…\", no tool/function names, no step-by-step. Just talk.",
            "- Keep it short, like a real group chat. To pull in a teammate, "
            "@mention them by name (e.g. @Name); say @all for everyone — but only "
            "when you genuinely need them, not as a reflex.",
            "- You may ONLY @mention a current channel member listed above. Do "
            "NOT @mention anyone who is not in that list — they are not in the "
            "channel and cannot see or answer it. If you want someone else "
            "involved, ask the user to add them instead of @mentioning them.",
        ]
        return "\n".join(lines)

    def _extract_team_mentions(
        self, text: str, member_map: Dict[str, str]
    ) -> List[str]:
        """Resolve @mentions in an agent's reply to channel-member agent_ids
        (or ["@everyone"] for @all/@everyone), so a hand-off pulls teammates in."""
        tokens = {t.lower() for t in re.findall(r"@([\w一-鿿]+)", text or "")}
        if not tokens:
            return []
        if "all" in tokens or "everyone" in tokens:
            return ["@everyone"]
        out: List[str] = []
        for aid, name in member_map.items():
            nm = (name or aid).lower()
            first = nm.split()[0] if nm.split() else nm
            if nm in tokens or first in tokens or any(
                len(t) >= 2 and nm.startswith(t) for t in tokens
            ):
                out.append(aid)
        return out

    async def _team_cascade_depth(self, channel_id: str) -> int:
        """How many consecutive agent (non-user) messages end the channel — i.e.
        how many agent hops have happened since the last human message. A user
        message resets this to 0 on its next turn."""
        ph = self._bus._db.placeholder
        rows = await self._bus._db.execute(
            f"SELECT from_agent FROM bus_messages WHERE channel_id = {ph} "
            f"ORDER BY created_at DESC LIMIT {MAX_TEAM_AGENT_HOPS + 2}",
            (channel_id,),
        )
        depth = 0
        for r in rows or []:
            if str(r["from_agent"]).startswith(USER_SENDER_PREFIX):
                break
            depth += 1
        return depth

    def _build_prompt(
        self, messages: List[BusMessage], owner_user_id: str = "", owner_name: str = ""
    ) -> str:
        # NOTE: this builds the full EXECUTION prompt (peer messages + the
        # repeated Owner-Relay boilerplate). For narrative retrieval, embed
        # build_bus_anchor(messages) instead — see the 2026-06-01 design doc.
        """
        Build a prompt from a list of pending messages.

        Includes all messages in the batch so the agent has full context.

        If `owner_user_id` is known, appends an owner-relay directive telling
        the agent it MUST call send_message_to_user_directly(user_id=<owner>,
        ...) to surface the peer exchange back into the owner's chat. Without
        this directive, agents treat peer exchanges as self-contained (they
        reply to the peer or stay silent), and the original owner who asked
        "go talk to agent B for me" never hears back — the reply only lands
        in the Inbox. observed as a silent-failure UX issue in production.
        """
        lines = ["[Message Bus - Incoming Messages]", ""]
        for msg in messages:
            lines.append(
                f"From: {msg.from_agent}\n"
                f"Time: {msg.created_at}\n"
                f"{msg.content}\n"
            )

        if owner_user_id:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Owner Relay — REQUIRED")
            lines.append("")
            lines.append(
                f"Your owner **{owner_name or owner_user_id}** originally asked "
                f"you to contact this peer agent. They are waiting in chat for "
                f"the answer."
            )
            lines.append("")
            lines.append(
                "The owner's chat view does NOT automatically receive the "
                "peer's reply. The ONLY channel that surfaces this exchange "
                "to the owner is `send_message_to_user_directly`. If you do "
                "not call it, the owner sees nothing — they only know "
                "there's a new entry in some inbox they may not be looking "
                "at. This is a silent-failure pattern we explicitly want to "
                "avoid."
            )
            lines.append("")
            lines.append("**What to do this turn:**")
            lines.append(
                "1. Understand the peer reply above."
            )
            lines.append(
                "2. If the peer's reply answers / progresses the owner's "
                "original request → call "
                f"`send_message_to_user_directly(agent_id=<you>, "
                f"user_id=\"{owner_user_id}\", content=<summary + peer "
                "quote>)`. Make the summary actionable: what did the peer "
                "say, what does it mean for the owner's task, what's next."
            )
            lines.append(
                "3. If the peer needs a clarifying follow-up from you → "
                "send it via `bus_send_to_agent`, THEN also call "
                "`send_message_to_user_directly` with a short status "
                "update (\"asked peer for X, waiting for clarification\") "
                "so the owner knows the thread is alive."
            )
            lines.append(
                "4. Silence is the wrong default. Only stay silent if the "
                "peer message is truly irrelevant (e.g. a closing "
                "acknowledgment you already reported to the owner)."
            )

        return "\n".join(lines)

    async def _invoke_runtime(
        self,
        agent_id: str,
        sender_agent_id: str,
        prompt: str,
        channel_id: str,
        trigger_message_id: str = "",
        retrieval_anchor: str = "",
    ) -> str:
        """
        Invoke AgentRuntime.run() for the given agent with the prompt.

        Returns the collected agent response text.

        Raises:
            RuntimeError: If AgentRuntime cannot be imported or execution fails.
        """
        try:
            from xyz_agent_context.agent_runtime.client import (
                get_agent_runtime_client,
            )
            from xyz_agent_context.schema import WorkingSource
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        collection = await get_agent_runtime_client().run_and_collect(
            agent_id=agent_id,
            user_id=sender_agent_id,
            input_content=prompt,
            working_source=WorkingSource.MESSAGE_BUS,
            trigger_extra_data={
                "bus_channel_id": channel_id,
                "retrieval_anchor": retrieval_anchor,
                "trigger_id": (
                    f"bus_{trigger_message_id}"
                    if trigger_message_id
                    else f"bus_chan_{channel_id}"
                ),
            },
        )

        # Error path (Bug 2): previously the loop only checked
        # AGENT_RESPONSE; if the agent run errored (e.g. owner removed
        # their provider, system default exhausted) the sender agent got
        # an empty string and had to guess why. Now we surface the error
        # inline so the sender sees what went wrong.
        if collection.is_error:
            logger.warning(
                f"[MessageBusTrigger] agent {agent_id} run failed in "
                f"channel {channel_id}: {collection.error.error_type}: "
                f"{collection.error.error_message}"
            )
            return (
                f"⚠️ I couldn't process your message right now "
                f"({collection.error.error_type}). {collection.error.error_message}"
            )

        return collection.output_text

    async def _write_to_inbox(
        self, agent_id: str, channel_id: str,
        trigger_message: BusMessage, agent_response: str,
    ) -> None:
        """Write the agent's response to the recipient user's inbox.

        Uses `InboxRepository.create_message` (the canonical writer) so
        the row shape stays in sync with the `inbox_table` schema —
        previous hand-written `db.insert("inbox_table", ...)` referenced
        `agent_id` / `owner_user_id` / `updated_at` columns that don't
        exist and omitted the required `message_id`, producing
        `Unknown column 'agent_id' in 'field list'` 13 times in 3
        hours on EC2 2026-05-18.
        """
        try:
            import uuid

            from xyz_agent_context.repository.inbox_repository import InboxRepository
            from xyz_agent_context.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )
            from xyz_agent_context.utils.db_factory import get_db_client

            db = await get_db_client()
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                logger.warning(f"Cannot write to inbox: agent {agent_id} not found")
                return
            recipient_user_id = agent_row.get("created_by", "")
            if not recipient_user_id:
                logger.warning(
                    f"Cannot write to inbox: agent {agent_id} has no created_by"
                )
                return

            repo = InboxRepository(db)
            await repo.create_message(
                user_id=recipient_user_id,
                message_id=f"bus_{uuid.uuid4().hex[:16]}",
                title=f"Message Bus: {trigger_message.from_agent}",
                content=agent_response,
                message_type=InboxMessageType.MESSAGE_BUS,
                source=MessageSource(type="message_bus", id=channel_id),
            )
            logger.info(
                f"Wrote MessageBus result to inbox for user {recipient_user_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")


async def _get_bus() -> LocalMessageBus:
    """Create and return a LocalMessageBus instance from environment config.

    Works with both SQLite (local) and MySQL (cloud) backends — LocalMessageBus
    is a misnomer; it's a database-backed bus that runs against any backend.
    """
    from xyz_agent_context.utils.db_factory import get_db_client

    db = await get_db_client()
    backend = db._backend

    # Ensure all tables exist (schema_registry covers all 26 tables including bus)
    from xyz_agent_context.utils.schema_registry import auto_migrate
    await auto_migrate(backend)

    # Initialise the system-default quota subsystem so bus-triggered
    # agent turns can fall back to the free-tier config when the owner
    # hasn't configured their own provider.
    from xyz_agent_context.agent_framework.quota_service import (
        bootstrap_quota_subsystem,
    )
    await bootstrap_quota_subsystem(db)

    return LocalMessageBus(backend=backend)


async def main() -> None:
    """Entry point for standalone execution."""
    logger.info("Starting MessageBusTrigger...")
    bus = await _get_bus()
    trigger = MessageBusTrigger(bus=bus)

    try:
        await trigger.start()
    except KeyboardInterrupt:
        trigger.stop()
        logger.info("MessageBusTrigger stopped by user")


if __name__ == "__main__":
    from xyz_agent_context.utils.logging import setup_logging
    setup_logging("message_bus_trigger")
    try:
        asyncio.run(main())
    finally:
        asyncio.run(logger.complete())
