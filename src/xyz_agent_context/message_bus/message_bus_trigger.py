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
import json
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
                logger.error(f"MessageBusTrigger poll cycle error: {e}")

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
        # Use %s — auto-translated for SQLite by AsyncDatabaseClient
        rows = await self._bus._db.execute(
            "SELECT channel_type, created_by FROM bus_channels WHERE channel_id = %s",
            (channel_id,),
        )
        if rows:
            return (
                rows[0].get("channel_type", "group"),
                rows[0].get("created_by", ""),
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
        """Process pending messages for an agent. Returns True if messages handled."""
        async with self._semaphore:
            try:
                pending = await self._bus.get_pending_messages(agent_id)
                if not pending:
                    return False

                by_channel: Dict[str, List[BusMessage]] = defaultdict(list)
                for msg in pending:
                    by_channel[msg.channel_id].append(msg)

                handled_any = False
                for channel_id, messages in by_channel.items():
                    # Skip Lark channels — they are managed by LarkTrigger, not MessageBusTrigger
                    if channel_id.startswith("lark_"):
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
                logger.error(
                    f"MessageBusTrigger: error processing agent {agent_id}: {e}"
                )
                return False

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
            # Build prompt from messages
            prompt = self._build_prompt(messages)

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages)"
            )

            # Call AgentRuntime
            response_text = await self._invoke_runtime(
                agent_id=agent_id,
                sender_agent_id=trigger_message.from_agent,
                prompt=prompt,
                channel_id=channel_id,
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
            logger.error(
                f"MessageBusTrigger: failed to process channel {channel_id} "
                f"for agent {agent_id}: {e}"
            )
            # Record failure for the trigger message
            await self._bus.record_failure(
                message_id=trigger_message.message_id,
                agent_id=agent_id,
                error=str(e),
            )

    def _build_prompt(self, messages: List[BusMessage]) -> str:
        """
        Build a prompt from a list of pending messages.

        Includes all messages in the batch so the agent has full context.
        """
        lines = ["[Message Bus - Incoming Messages]", ""]
        for msg in messages:
            lines.append(
                f"From: {msg.from_agent}\n"
                f"Time: {msg.created_at}\n"
                f"{msg.content}\n"
            )
        return "\n".join(lines)

    async def _invoke_runtime(
        self,
        agent_id: str,
        sender_agent_id: str,
        prompt: str,
        channel_id: str,
    ) -> str:
        """
        Invoke AgentRuntime.run() for the given agent with the prompt.

        Returns the collected agent response text.

        Raises:
            RuntimeError: If AgentRuntime cannot be imported or execution fails.
        """
        try:
            from xyz_agent_context.agent_runtime import AgentRuntime
            from xyz_agent_context.agent_runtime.logging_service import LoggingService
            from xyz_agent_context.schema import MessageType, WorkingSource
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
        final_output: list[str] = []

        async for response in runtime.run(
            agent_id=agent_id,
            user_id=sender_agent_id,
            input_content=prompt,
            working_source=WorkingSource.MESSAGE_BUS,
            trigger_extra_data={"bus_channel_id": channel_id},
        ):
            if hasattr(response, "message_type"):
                if response.message_type == MessageType.AGENT_RESPONSE:
                    if hasattr(response, "delta") and response.delta:
                        final_output.append(response.delta)

        return "".join(final_output)

    async def _write_to_inbox(
        self, agent_id: str, channel_id: str,
        trigger_message: BusMessage, agent_response: str,
    ) -> None:
        """Write the agent's response to the user's inbox."""
        try:
            from xyz_agent_context.utils.db_factory import get_db_client
            db = await get_db_client()
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                logger.warning(f"Cannot write to inbox: agent {agent_id} not found")
                return
            owner_user_id = agent_row.get("created_by", "")
            from xyz_agent_context.utils.timezone import utc_now
            now = utc_now()
            inbox_data = {
                "agent_id": agent_id,
                "owner_user_id": owner_user_id,
                "message_type": "channel_message",
                "title": f"Message Bus: {trigger_message.from_agent}",
                "content": agent_response,
                "source": json.dumps({
                    "type": "message_bus",
                    "channel_id": channel_id,
                    "from_agent": trigger_message.from_agent,
                    "original_message": trigger_message.content[:500],
                }),
                "is_read": False,
                "created_at": now,
                "updated_at": now,
            }
            await db.insert("inbox_table", inbox_data)
            logger.info(f"Wrote MessageBus result to inbox for user {owner_user_id}")
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
    asyncio.run(main())
