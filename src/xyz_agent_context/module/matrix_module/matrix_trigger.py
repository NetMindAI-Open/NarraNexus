"""
@file_name: matrix_trigger.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixTrigger — background message polling process

Single-process poller that monitors all Agent Matrix credentials for new messages.
Architecture: 1 Poller + N Workers.

Key design decisions:
- Per-room batching: multiple messages from the same room are collapsed into ONE
  AgentRuntime call using the latest message as trigger. The conversation history
  already includes older messages, so the agent sees the full picture.
- Two-tier dedup (in-memory + DB): survives process restarts without re-processing.
- LoggingService disabled in workers: the trigger process has its own file logger
  via service_logger. AgentRuntime workers share this logger instead of each creating
  their own log handler (prevents concurrent file operation race conditions).

Usage:
    uv run python -m xyz_agent_context.module.matrix_module.matrix_trigger
"""

from __future__ import annotations

import asyncio
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from loguru import logger

from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.inbox_schema import InboxMessageType
from xyz_agent_context.schema.runtime_message import MessageType
from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.utils import DatabaseClient, get_db_client

from ._matrix_credential_manager import MatrixCredentialManager, MatrixCredential
from ._matrix_dedup import MatrixEventDedup
from .matrix_client import NexusMatrixClient
from .matrix_context_builder import MatrixContextBuilder
from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig


# === Adaptive polling constants ===
POLL_MIN_INTERVAL = 15   # Seconds — active agent
POLL_MAX_INTERVAL = 120  # Seconds — idle agent
POLL_STEP_UP = 15        # Seconds — increase per idle cycle
POLL_INITIAL = 30        # Seconds — initial interval

# === Safety net: prevent extreme agent-to-agent loops ===
ROOM_RATE_LIMIT_MAX = 20        # Max triggers per agent per room in the window
ROOM_RATE_LIMIT_WINDOW = 1800   # Window in seconds (30 min)

# === Dedup cleanup ===
DEDUP_CLEANUP_INTERVAL = 3600   # Run cleanup every hour
DEDUP_RETENTION_DAYS = 7        # Keep processed events for 7 days


@dataclass
class RoomBatch:
    """Messages from one room, collapsed into a single trigger."""
    room_id: str
    room_name: str
    latest_message: Dict[str, Any]    # The newest message (used as trigger)
    all_event_ids: List[str]          # All event IDs to mark as processed


@dataclass
class AgentTask:
    """
    One unit of work for a worker: all pending rooms for one agent.

    Replaces the old MessageTask (1 message = 1 task).
    Now: 1 agent = 1 task, containing batched messages per room.
    """
    credential: MatrixCredential
    room_batches: List[RoomBatch] = field(default_factory=list)


class MatrixTrigger:
    """
    Background polling service for Matrix messages.

    Architecture: 1 Poller coroutine + N Worker coroutines.
    Polls all active Agent credentials for new messages via heartbeat,
    then dispatches batched processing to the worker pool.

    Args:
        base_poll_interval: Base polling cycle interval in seconds
        max_workers: Max concurrent message processing workers
        history_config: Conversation history loading config
    """

    def __init__(
        self,
        base_poll_interval: int = 30,
        max_workers: int = 5,
        history_config: Optional[ChannelHistoryConfig] = None,
    ):
        self.base_poll_interval = base_poll_interval
        self.max_workers = max_workers
        self.history_config = history_config or ChannelHistoryConfig()

        self._db: Optional[DatabaseClient] = None
        self._cred_mgr: Optional[MatrixCredentialManager] = None
        self._dedup: Optional[MatrixEventDedup] = None

        self.running = False
        self._task_queue: asyncio.Queue[AgentTask] = asyncio.Queue()
        self._processing_agents: Set[str] = set()

        # Safety net: track (agent_id, room_id) → list of timestamps
        self._room_activity: Dict[str, List[datetime]] = {}
        self._workers: List[asyncio.Task] = []
        self._poller_task: Optional[asyncio.Task] = None
        self._last_dedup_cleanup: datetime = datetime.now(timezone.utc)

    @property
    def db(self) -> DatabaseClient:
        if self._db is None:
            raise RuntimeError("Database not initialized. Call start() first.")
        return self._db

    @property
    def cred_mgr(self) -> MatrixCredentialManager:
        if self._cred_mgr is None:
            self._cred_mgr = MatrixCredentialManager(self.db)
        return self._cred_mgr

    @property
    def dedup(self) -> MatrixEventDedup:
        if self._dedup is None:
            self._dedup = MatrixEventDedup(self.db)
        return self._dedup

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """
        Start the MatrixTrigger (Worker Pool mode).

        1 Poller + N Workers. Runs until stop() is called.
        """
        if self._db is None:
            self._db = await get_db_client()
            logger.info("MatrixTrigger: database client initialized")

        # Ensure dedup table exists
        await self._ensure_dedup_table()

        logger.info("=" * 60)
        logger.info("MatrixTrigger starting (Worker Pool mode)")
        logger.info(f"  Base poll interval: {self.base_poll_interval}s")
        logger.info(f"  Max workers: {self.max_workers}")
        logger.info(f"  History loading: {self.history_config.load_conversation_history}")
        logger.info("=" * 60)

        self.running = True

        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)

        self._poller_task = asyncio.create_task(self._poller())

        try:
            await asyncio.gather(self._poller_task, *self._workers)
        except asyncio.CancelledError:
            logger.info("MatrixTrigger tasks cancelled")

        logger.info("MatrixTrigger stopped")

    async def _ensure_dedup_table(self) -> None:
        """Create dedup table if it doesn't exist (idempotent)."""
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS `matrix_processed_events` (
                    `event_id` VARCHAR(255) NOT NULL,
                    `agent_id` VARCHAR(64) NOT NULL,
                    `processed_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (`event_id`, `agent_id`),
                    INDEX `idx_processed_at` (`processed_at`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                fetch=False,
            )
        except Exception as e:
            logger.warning(f"Failed to ensure dedup table: {e}")

    async def stop(self) -> None:
        """Gracefully stop the trigger."""
        logger.info("Stopping MatrixTrigger gracefully...")
        self.running = False

        try:
            await asyncio.wait_for(self._task_queue.join(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for queue to drain, forcing shutdown")

        if self._poller_task:
            self._poller_task.cancel()
        for w in self._workers:
            w.cancel()

        await asyncio.gather(
            self._poller_task, *self._workers, return_exceptions=True
        )
        self._workers.clear()
        self._poller_task = None

    # =========================================================================
    # Helpers
    # =========================================================================

    def _check_room_rate_limit(self, agent_id: str, room_id: str) -> bool:
        """
        Check if agent has exceeded the safety-net rate limit for a room.

        Returns True if the message should be SKIPPED (rate limited).
        High-threshold safety net — normal conversations never hit this.
        """
        key = f"{agent_id}:{room_id}"
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=ROOM_RATE_LIMIT_WINDOW)

        timestamps = self._room_activity.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= ROOM_RATE_LIMIT_MAX:
            logger.warning(
                f"Safety net: agent {agent_id} hit rate limit in room {room_id} "
                f"({len(timestamps)} triggers in {ROOM_RATE_LIMIT_WINDOW}s). Skipping."
            )
            self._room_activity[key] = timestamps
            return True

        timestamps.append(now)
        self._room_activity[key] = timestamps
        return False

    def _is_mentioned(self, msg: Dict[str, Any], cred: MatrixCredential) -> bool:
        """
        Check if this agent is mentioned in the message via m.mentions.

        Matrix mention format (MSC3952 / Matrix v1.7+):
        The sending client must include an "m.mentions" key in the event content:

            {
                "msgtype": "m.text",
                "body": "@alice Hello!",
                "m.mentions": {
                    "user_ids": ["@alice:server"]       # mention specific users
                }
            }

        To mention everyone in a room (@room):
            "m.mentions": { "room": true }

        Detection rules:
        - m.mentions.room == true          → @everyone, all agents triggered
        - m.mentions.user_ids contains id  → that specific agent triggered
        - No m.mentions at all             → not mentioned (skip in group chats)
        """
        content = msg.get("content")
        if not isinstance(content, dict):
            return False

        mentions = content.get("m.mentions")
        if not isinstance(mentions, dict):
            return False

        if mentions.get("room") is True:
            return True

        mentioned_ids = mentions.get("user_ids", [])
        if isinstance(mentioned_ids, list) and cred.matrix_user_id in mentioned_ids:
            return True

        return False

    async def _get_room_meta(
        self, client: "NexusMatrixClient", api_key: str, room_id: str
    ) -> Dict[str, Any]:
        """
        Get room metadata from API (no cache).

        Always fetches fresh data to avoid stale member_count or creator
        causing incorrect DM detection or mention filtering.
        """
        info = await client.get_room_info(api_key=api_key, room_id=room_id)
        return {
            "member_count": info.get("member_count", 0) if info else 0,
            "creator": info.get("creator") if info else None,
        }

    # =========================================================================
    # Poller
    # =========================================================================

    async def _poller(self) -> None:
        """Poller coroutine: iterate over due credentials and check for messages."""
        while self.running:
            try:
                await self._poll_cycle()

                # Periodic dedup cleanup
                now = datetime.now(timezone.utc)
                if (now - self._last_dedup_cleanup).total_seconds() > DEDUP_CLEANUP_INTERVAL:
                    await self.dedup.cleanup_expired(DEDUP_RETENTION_DAYS)
                    self._last_dedup_cleanup = now

                await asyncio.sleep(self.base_poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poller error: {e}")
                await asyncio.sleep(self.base_poll_interval)

    async def _poll_cycle(self) -> None:
        """Execute one polling cycle across all due credentials."""
        now = datetime.now(timezone.utc)
        due_creds = await self.cred_mgr.get_due_credentials(now)

        if not due_creds:
            return

        logger.debug(f"Polling {len(due_creds)} due credential(s)")

        for cred in due_creds:
            if cred.agent_id in self._processing_agents:
                continue

            try:
                await self._check_credential(cred, now)
            except Exception as e:
                logger.error(f"Error checking credential for {cred.agent_id}: {e}")

    async def _check_credential(self, cred: MatrixCredential, now: datetime) -> None:
        """
        Check a single credential for new messages via heartbeat.

        Core change from the old design: per-room message batching.
        For each room, only the LATEST passing message becomes the trigger.
        All passing event IDs are marked as processed in dedup.
        The agent sees the full conversation history in its prompt anyway.
        """
        client = NexusMatrixClient(server_url=cred.server_url)
        try:
            hb = await client.heartbeat(api_key=cred.api_key)

            next_poll = now + timedelta(seconds=POLL_INITIAL)

            try:
                room_batches: List[RoomBatch] = []

                # --- Handle pending invitations (auto-accept + trigger runtime) ---
                pending_invites = hb.get("pending_invites", []) if hb else []
                for invite in pending_invites:
                    invite_room_id = invite.get("room_id", "")
                    inviter = invite.get("inviter", "")
                    if not invite_room_id:
                        continue

                    joined = await client.join_room(
                        api_key=cred.api_key, room_id=invite_room_id
                    )
                    if joined:
                        logger.info(
                            f"Auto-accepted invite for {cred.agent_id}: "
                            f"room={invite_room_id}, inviter={inviter}"
                        )
                        room_batches.append(RoomBatch(
                            room_id=invite_room_id,
                            room_name="",
                            latest_message={
                                "type": "invite",
                                "sender": inviter,
                                "body": f"You have been invited to a room by {inviter}. Say hello and introduce yourself.",
                                "room_id": invite_room_id,
                                "room_name": "",
                            },
                            all_event_ids=[],
                        ))
                    else:
                        logger.warning(
                            f"Failed to join room {invite_room_id} for {cred.agent_id}"
                        )

                # --- Handle new messages ---
                if hb and hb.get("has_updates"):
                    rooms_with_unread = hb.get("rooms_with_unread", [])

                    logger.debug(
                        f"[{cred.agent_id}] heartbeat has_updates=True, "
                        f"rooms_with_unread={len(rooms_with_unread)}"
                    )

                    for room_info in rooms_with_unread:
                        room_id = room_info.get("room_id", "")
                        if not room_id:
                            continue

                        batch = await self._collect_room_messages(
                            client, cred, room_id, room_info
                        )
                        if batch:
                            room_batches.append(batch)

                        # Always mark room as read (even if all messages filtered out)
                        try:
                            await client.mark_read(
                                api_key=cred.api_key, room_id=room_id
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to mark_read for {cred.agent_id} "
                                f"room {room_id}: {e}"
                            )
                else:
                    logger.debug(
                        f"[{cred.agent_id}] heartbeat has_updates=False "
                        f"(no unread rooms)"
                    )

                # --- Enqueue one task per agent (if there's anything to process) ---
                if room_batches:
                    task = AgentTask(
                        credential=cred,
                        room_batches=room_batches,
                    )
                    await self._task_queue.put(task)

                    total_events = sum(len(b.all_event_ids) for b in room_batches)
                    logger.info(
                        f"Enqueued {len(room_batches)} room(s) / "
                        f"{total_events} event(s) for agent {cred.agent_id}"
                    )
                    next_poll = now + timedelta(seconds=POLL_MIN_INTERVAL)
                else:
                    # Adaptive: lengthen interval for idle agents
                    current_interval = POLL_INITIAL
                    npt = cred.next_poll_time
                    if npt:
                        if npt.tzinfo is None:
                            npt = npt.replace(tzinfo=timezone.utc)
                    if npt and npt <= now:
                        last_interval = (now - npt).total_seconds() + POLL_STEP_UP
                        current_interval = min(
                            max(last_interval, POLL_MIN_INTERVAL),
                            POLL_MAX_INTERVAL,
                        )
                    next_poll = now + timedelta(seconds=current_interval)

            except Exception as e:
                logger.warning(f"Error fetching messages for {cred.agent_id}: {e}")

            await self.cred_mgr.update_next_poll_time(cred.agent_id, next_poll)

            if hb and hb.get("next_batch"):
                await self.cred_mgr.update_sync_token(
                    cred.agent_id, hb["next_batch"]
                )

        finally:
            await client.close()

    async def _collect_room_messages(
        self,
        client: NexusMatrixClient,
        cred: MatrixCredential,
        room_id: str,
        room_info: Dict[str, Any],
    ) -> Optional[RoomBatch]:
        """
        Collect passing messages from one room, return a RoomBatch.

        Filters: skip own → dedup (L1+L2) → DM/mention → rate limit.
        Only the LATEST passing message becomes the trigger.
        All passing event IDs are recorded for dedup.

        Returns None if no messages pass all filters.
        """
        messages = await client.get_messages(
            api_key=cred.api_key,
            room_id=room_id,
            limit=10,
        )
        if not messages:
            return None

        # Fetch room metadata
        room_meta = await self._get_room_meta(client, cred.api_key, room_id)
        mc = room_meta["member_count"]
        is_dm = mc > 0 and mc <= 2
        is_room_creator = room_meta["creator"] == cred.matrix_user_id

        logger.debug(
            f"[{cred.agent_id}] room={room_id} "
            f"member_count={mc} is_dm={is_dm} "
            f"is_room_creator={is_room_creator} "
            f"msgs_fetched={len(messages)}"
        )

        # Collect event_ids for batch dedup check (skip own first)
        other_messages = []
        for msg in messages:
            if msg.get("sender") == cred.matrix_user_id:
                continue
            other_messages.append(msg)

        if not other_messages:
            return None

        # Batch dedup check: L1 cache + L2 DB
        event_ids = [m.get("event_id", "") for m in other_messages if m.get("event_id")]
        already_processed = await self.dedup.filter_processed(cred.agent_id, event_ids)

        # Filter messages
        passing_messages = []
        passing_event_ids = []
        all_event_ids_to_mark = []  # Both passing and filtered — all get deduped

        for msg in other_messages:
            event_id = msg.get("event_id", "")
            sender = msg.get("sender", "?")

            if event_id in already_processed:
                logger.debug(
                    f"[{cred.agent_id}] DEDUP skip "
                    f"event={event_id[:20]} from={sender}"
                )
                continue

            # Mention filter for group rooms (room creator sees all messages)
            mentioned = self._is_mentioned(msg, cred)
            if not is_dm and not is_room_creator and not mentioned:
                content = msg.get("content")
                has_mentions = isinstance(content, dict) and "m.mentions" in content
                logger.debug(
                    f"[{cred.agent_id}] MENTION skip "
                    f"event={event_id[:20]} from={sender} "
                    f"has_content={content is not None} "
                    f"has_m.mentions={has_mentions}"
                )
                all_event_ids_to_mark.append(event_id)
                continue

            # Rate limit check
            if self._check_room_rate_limit(cred.agent_id, room_id):
                logger.debug(
                    f"[{cred.agent_id}] RATE_LIMIT skip event={event_id[:20]}"
                )
                continue

            passing_messages.append(msg)
            passing_event_ids.append(event_id)
            all_event_ids_to_mark.append(event_id)

        # Mark ALL encountered (non-deduped) events as processed —
        # both passing and filtered (mention skip, etc.)
        if all_event_ids_to_mark:
            await self.dedup.mark_processed(cred.agent_id, all_event_ids_to_mark)

        if not passing_messages:
            return None

        # Use the LATEST passing message as trigger.
        # Messages come newest-first (direction="b"), so index 0 is newest.
        latest = passing_messages[0]

        logger.debug(
            f"[{cred.agent_id}] BATCH room={room_id} "
            f"passing={len(passing_messages)} "
            f"trigger_event={latest.get('event_id', '')[:20]} "
            f"from={latest.get('sender', '?')}"
        )

        return RoomBatch(
            room_id=room_id,
            room_name=room_info.get("room_name", ""),
            latest_message={
                **latest,
                "room_id": room_id,
                "room_name": room_info.get("room_name", ""),
            },
            all_event_ids=passing_event_ids,
        )

    # =========================================================================
    # Workers
    # =========================================================================

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine: process agent tasks from the queue."""
        while True:
            try:
                task = await self._task_queue.get()
                try:
                    self._processing_agents.add(task.credential.agent_id)
                    await self._process_task(task, worker_id)
                finally:
                    self._processing_agents.discard(task.credential.agent_id)
                    self._task_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Unexpected error: {e}")

    async def _process_task(self, task: AgentTask, worker_id: int) -> None:
        """
        Process all room batches for one agent.

        Each room batch triggers one AgentRuntime call with the latest message.
        The conversation history in the prompt includes all older messages.
        """
        cred = task.credential
        agent_id = cred.agent_id

        for batch in task.room_batches:
            try:
                await self._process_room_batch(batch, cred, worker_id)
            except Exception as e:
                logger.error(
                    f"[Worker {worker_id}] Failed to process room {batch.room_id} "
                    f"for {agent_id}: {e}"
                )

    async def _process_room_batch(
        self, batch: RoomBatch, cred: MatrixCredential, worker_id: int
    ) -> None:
        """
        Process a single room batch through AgentRuntime.

        Flow:
        1. Build prompt from latest message via MatrixContextBuilder
        2. Call AgentRuntime.run() ONCE (not per-message)
        3. Write result to Inbox
        """
        event = batch.latest_message
        agent_id = cred.agent_id
        sender = event.get("sender", "unknown")
        body = event.get("body", "")[:100]

        logger.info(
            f"[Worker {worker_id}] Processing {len(batch.all_event_ids)} event(s) "
            f"for {agent_id} in {batch.room_id} — trigger: {body}..."
        )

        # 1. Build prompt
        client = NexusMatrixClient(server_url=cred.server_url)
        try:
            builder = MatrixContextBuilder(
                message_event=event,
                credential=cred,
                client=client,
                agent_id=agent_id,
            )
            prompt = await builder.build_prompt(self.history_config)
        finally:
            await client.close()

        # 2. Create ChannelTag
        channel_tag = ChannelTag.matrix(
            sender_name=event.get("sender_display_name", sender),
            sender_id=sender,
            room_id=batch.room_id,
            room_name=batch.room_name,
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"

        # 3. Call AgentRuntime with logging DISABLED
        #    (trigger process already has its own file logger via service_logger;
        #     per-worker LoggingService causes file race conditions)
        from xyz_agent_context.agent_runtime import AgentRuntime
        from xyz_agent_context.agent_runtime.logging_service import LoggingService

        runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
        final_output = []

        async for response in runtime.run(
            agent_id=agent_id,
            user_id=sender,
            input_content=tagged_prompt,
            working_source=WorkingSource.MATRIX,
            trigger_extra_data={"channel_tag": channel_tag.to_dict()},
        ):
            if hasattr(response, "message_type"):
                if response.message_type == MessageType.AGENT_RESPONSE:
                    if hasattr(response, "delta") and response.delta:
                        final_output.append(response.delta)

        content = "".join(final_output)

        # 4. Write to Inbox
        await self._write_to_inbox(cred, event, content, room_id=batch.room_id)

        logger.info(
            f"[Worker {worker_id}] Batch processed for {agent_id} "
            f"room={batch.room_id}, output length: {len(content)}"
        )

    async def _write_to_inbox(
        self,
        cred: MatrixCredential,
        event: Dict[str, Any],
        agent_output: str,
        room_id: str = "",
    ) -> None:
        """Write Matrix conversation result to the Agent owner's Inbox."""
        import json as _json

        try:
            sender = event.get("sender_display_name", event.get("sender", "Unknown"))
            sender_id = event.get("sender", "")
            room_name = event.get("room_name", "Matrix Room")
            actual_room_id = room_id or event.get("room_id", "")

            title = f"Matrix: {sender} in {room_name}"
            content = (
                f"**From**: {sender}\n"
                f"**Room**: {room_name}\n"
                f"**Message**: {event.get('body', '')}\n\n"
                f"---\n\n"
                f"**Your response**:\n{agent_output}"
            )

            msg_id = f"msg_{uuid4().hex[:16]}"

            from xyz_agent_context.repository import AgentRepository
            agent_repo = AgentRepository(self.db)
            try:
                agent = await agent_repo.get_by_id(cred.agent_id)
                owner_user_id = agent.created_by if agent else ""
            except Exception as e:
                logger.warning(f"Failed to look up agent owner for {cred.agent_id}: {e}")
                owner_user_id = ""

            if not owner_user_id:
                logger.warning(
                    f"Cannot write to inbox: no owner found for agent {cred.agent_id}"
                )
                return

            source_json = _json.dumps({
                "type": "matrix",
                "id": cred.agent_id,
                "room_id": actual_room_id,
                "room_name": room_name,
                "sender_id": sender_id,
                "sender_name": sender,
            }, ensure_ascii=False)

            from xyz_agent_context.utils import utc_now
            await self.db.insert("inbox_table", {
                "message_id": msg_id,
                "user_id": owner_user_id,
                "source": source_json,
                "event_id": event.get("event_id", "") or None,
                "message_type": InboxMessageType.CHANNEL_MESSAGE.value,
                "title": title,
                "content": content,
                "is_read": False,
                "created_at": utc_now(),
            })

        except Exception as e:
            logger.error(f"Failed to write Matrix message to inbox: {e}")


# =============================================================================
# Entry Points
# =============================================================================

def run_matrix_trigger(
    base_poll_interval: int = 30,
    max_workers: int = 5,
) -> None:
    """
    Run MatrixTrigger (called by ModuleRunner or standalone).
    """
    import xyz_agent_context.settings  # noqa: F401

    trigger = MatrixTrigger(
        base_poll_interval=base_poll_interval,
        max_workers=max_workers,
    )
    asyncio.run(trigger.start())


def main():
    """CLI entry point for MatrixTrigger."""
    parser = argparse.ArgumentParser(
        description="MatrixTrigger — Background Matrix Message Poller",
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=30,
        help="Base poll interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=5,
        help="Max concurrent workers (default: 5)",
    )
    parser.add_argument(
        "--no-history", action="store_true",
        help="Disable conversation history loading in prompts",
    )
    args = parser.parse_args()

    from xyz_agent_context.utils.service_logger import setup_service_logger
    setup_service_logger("matrix_trigger")

    logger.info("=" * 60)
    logger.info("MatrixTrigger — Background Matrix Message Poller")
    logger.info(f"  Poll interval: {args.interval}s")
    logger.info(f"  Max workers: {args.workers}")
    logger.info(f"  History: {'disabled' if args.no_history else 'enabled'}")
    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop")

    history_config = ChannelHistoryConfig(
        load_conversation_history=not args.no_history,
    )

    trigger = MatrixTrigger(
        base_poll_interval=args.interval,
        max_workers=args.workers,
        history_config=history_config,
    )

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(trigger.start())


if __name__ == "__main__":
    main()
