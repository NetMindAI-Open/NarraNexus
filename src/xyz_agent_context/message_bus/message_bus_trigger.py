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
import time
from collections import defaultdict
from typing import Dict, List

from loguru import logger

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.schemas import BusMessage

# Poll interval in seconds
POLL_INTERVAL = 10

# Maximum concurrent agent processing workers
MAX_WORKERS = 3

# Rate limiting constants
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 1800  # 30 minutes in seconds

# Adaptive polling constants
POLL_MIN_INTERVAL = 10
POLL_MAX_INTERVAL = 120
POLL_STEP_UP = 15


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
                    # Skip IM-channel-owned channels — each has its own dedicated trigger
                    # (LarkTrigger, TelegramTrigger, SlackTrigger) that already processed
                    # the message. ChannelInboxWriter writes these to bus_messages purely
                    # for frontend Inbox display; re-consuming them here would fire
                    # AgentRuntime a second time and send duplicate replies.
                    _IM_CHANNEL_PREFIXES = ("lark_", "telegram_", "slack_")
                    if channel_id.startswith(_IM_CHANNEL_PREFIXES):
                        latest = max(messages, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(agent_id, channel_id, str(latest.created_at))
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
                            agent_id, channel_id, str(latest.created_at)
                        )
                        continue

                    # Rate limiting
                    if not self._check_rate_limit(agent_id, channel_id):
                        latest = max(relevant, key=lambda m: str(m.created_at))
                        await self._bus.ack_processed(
                            agent_id, channel_id, str(latest.created_at)
                        )
                        continue

                    trigger_msg = relevant[-1]
                    await self._handle_channel_batch(
                        agent_id, channel_id, relevant, trigger_msg
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
    ) -> None:
        """
        Handle a batch of messages from a single channel for an agent.

        Builds a prompt, invokes AgentRuntime, and on success advances the
        processing cursor. On failure, records the failure for retry tracking.
        """
        try:
            # Owner lookup up-front — used by both the prompt (to remind the
            # agent its owner is waiting in chat) and the inbox writer.
            owner_user_id = await self._get_agent_owner(agent_id)

            # Build prompt from messages
            prompt = self._build_prompt(messages, owner_user_id=owner_user_id)

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages)"
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

            # Write response to inbox
            if response_text:
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

    def _build_prompt(
        self, messages: List[BusMessage], owner_user_id: str = ""
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
                f"Your owner (user_id=`{owner_user_id}`) originally asked you "
                f"to contact this peer agent. They are waiting in chat for "
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
            from xyz_agent_context.agent_runtime import AgentRuntime
            from xyz_agent_context.agent_runtime.run_collector import collect_run
            from xyz_agent_context.schema import WorkingSource
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        runtime = AgentRuntime()
        collection = await collect_run(
            runtime,
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
