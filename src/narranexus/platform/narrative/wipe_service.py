"""
@file_name: wipe_service.py
@author: NarraNexus
@date: 2026-07-10
@description: Full "clear conversation & memory" wipe for one (agent, user).

Why this exists
---------------
The long-memory surface is NOT only the database. Narrative bodies live as
markdown on disk (`settings.narrative_markdown_path/<agent>/<user>/narratives`)
and every turn is written as a trajectory JSON
(`settings.trajectory_path/...`). The DB `narratives`/`events` rows are an
index that is rebuilt from those files on restart. So a DB-only "clear
history" left the agent remembering everything after the next restart — the
exact bug this service fixes.

Scoping / multi-tenant correctness
-----------------------------------
An agent is single-owner (`agents.created_by`), so the caller (the owner) is
clearing their own agent. That makes `agent_id` a safe scoping key.

- Conversation tables scope by narrative_id (only the caller's narratives),
  event_id (their events' stream frames), or the agent's ChatModule instances.
- The unified `memory_*` tables carry only `agent_id + scope_type + scope_id`
  (no user_id, no narrative_id — see schema_registry `_memory_kind_table`), so
  they can only be cleared by `agent_id`. That is intended for a full wipe.

IM conversation history IS cleared under `conversations`, and since 2026-08-17
it lives in `inbox_threads` / `inbox_thread_messages` — the inbox got its own
tables and `ChannelInboxWriter` is gone, so no IM pseudo-agent, channel or
membership row is created any more. Both are wiped by `agent_id`, which the
thread row carries and which `im_thread_id` now keys on, so one agent's wipe
cannot reach another agent's record even when both talk to the same chat.

This is load-bearing twice over. The IM record is the largest content class in
the system and the frontend renders it directly (`/api/agent-inbox`), so a wipe
that missed it would report success while the whole Lark/Telegram/WeChat
conversation stayed visible in the panel. And Telegram/WeChat read
`inbox_thread_messages` back as their conversation memory, so a "cleared" agent
would still recall everything — the same reason the bus mirror had to go.

`bus_messages` is still cleared for channels where this agent is the SOLE member,
because deployed databases still hold pre-2026-08-17 IM rows written by the old
writer. Channels shared with other agents are left untouched (deleting them
would corrupt other members' history). The channel bindings/membership
(`bus_channels`, `bus_channel_members`) are kept so the agent keeps receiving new
messages; only the message rows go.

NOTE the platform caveat: the IM platform (Lark/Telegram/WeChat) still holds
the real messages on ITS servers. This wipe clears NarraNexus's local mirror
and the agent's memory; it cannot delete anything on Lark. A tool that calls
the Lark API directly can still read history there.

Deliberately NOT touched: `inbox_table` (user-level, cross-agent),
`cost_records` (billing analytics), and — always — the agent's identity:
`agents`, credentials, `user_settings`, `instance_awareness` (persona), and the
system/capability `module_instances` (only `ChatModule` rows are removed).

DB is committed first (source of truth); disk deletes run after, best-effort,
each capturing its error so a filesystem hiccup never rolls back the DB. The
whole operation is idempotent — a second call is a no-op returning zeroes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from loguru import logger

from narranexus.platform.repository import InstanceRepository
from narranexus.platform.utils.db.schema_registry import MEMORY_KINDS
from .exporters import NarrativeMarkdownManager, TrajectoryRecorder
from .session_service import SessionService


@dataclass
class WipeResult:
    """What a wipe removed. Returned to the route for the API response."""
    scopes: List[str] = field(default_factory=list)
    narrative_ids: List[str] = field(default_factory=list)
    narratives_count: int = 0
    events_count: int = 0
    event_stream_count: int = 0
    chat_memory_count: int = 0
    chat_instances_count: int = 0
    report_memory_count: int = 0
    instance_links_count: int = 0
    agent_messages_count: int = 0
    bus_messages_count: int = 0
    bus_failures_count: int = 0
    inbox_threads_count: int = 0
    inbox_thread_messages_count: int = 0
    memory_rows_count: int = 0
    artifacts_count: int = 0
    disk_markdown_removed: bool = False
    disk_trajectories_removed: bool = False
    session_removed: bool = False
    disk_errors: List[str] = field(default_factory=list)


def _actors_contain_user(narrative_info_raw: Any, user_id: str) -> bool:
    """True if the narrative's actor list includes user_id.

    Mirrors the filter the chat-history route has always used: a narrative
    belongs to a user if that user is one of its actors.
    """
    if not narrative_info_raw:
        return False
    if isinstance(narrative_info_raw, str):
        try:
            info = json.loads(narrative_info_raw)
        except (json.JSONDecodeError, ValueError):
            return False
    else:
        info = narrative_info_raw
    if not isinstance(info, dict):
        return False
    return any(a.get("id") == user_id for a in info.get("actors", []) if isinstance(a, dict))


async def _resolve_user_narrative_ids(db_client, agent_id: str, user_id: str) -> List[str]:
    """Narrative ids under this agent whose actors include user_id."""
    rows = await db_client.get("narratives", filters={"agent_id": agent_id})
    return [
        r["narrative_id"]
        for r in rows
        if r.get("narrative_id") and _actors_contain_user(r.get("narrative_info"), user_id)
    ]


async def wipe_agent_data(
    db_client,
    agent_id: str,
    user_id: str,
    *,
    clear_conversations: bool,
    clear_memory: bool,
    narrative_base: Optional[str] = None,
    trajectory_base: Optional[str] = None,
    session_service: Optional[SessionService] = None,
) -> WipeResult:
    """Clear the selected scopes of an agent's data for one user.

    Args:
        db_client: AsyncDatabaseClient.
        agent_id: target agent (caller must be the owner — enforced by the route).
        user_id: the owner; scopes narrative/session/disk deletes.
        clear_conversations: delete conversation history + narratives +
            trajectories + chat instances + session.
        clear_memory: delete the agent's learned long-term memory (`memory_*`,
            consolidation queue, artifacts).
        narrative_base / trajectory_base: override on-disk roots (tests).
        session_service: override SessionService (tests); defaults to a new one.

    Returns:
        WipeResult with per-target counts and disk outcome.
    """
    result = WipeResult()
    if clear_conversations:
        result.scopes.append("conversations")
    if clear_memory:
        result.scopes.append("memory")

    # Only the conversations scope needs the narrative list; skip the query
    # entirely for a memory-only wipe.
    narrative_ids = (
        await _resolve_user_narrative_ids(db_client, agent_id, user_id)
        if clear_conversations else []
    )
    result.narrative_ids = narrative_ids

    # Collect event ids up front (needed to clear event_stream after events go).
    event_ids: List[str] = []
    if clear_conversations:
        for nid in narrative_ids:
            for ev in await db_client.get("events", filters={"narrative_id": nid}):
                if ev.get("event_id"):
                    event_ids.append(ev["event_id"])

    # Resolve the agent's chat-history instances (per-conversation, removable):
    # every instance of a module that provides chat history (ChatModule today).
    chat_instance_ids: List[str] = []
    if clear_conversations:
        from narranexus.platform.module_system import module_class_provides_chat_history

        instance_repo = InstanceRepository(db_client)
        chat_instance_ids = [
            i.instance_id for i in await instance_repo.get_by_agent(agent_id)
            if module_class_provides_chat_history(i.module_class)
        ]

    # ---- DB deletes (transactional) ----
    async with db_client.transaction():
        if clear_conversations:
            for nid in narrative_ids:
                result.events_count += await db_client.delete("events", {"narrative_id": nid})
                result.report_memory_count += await db_client.delete(
                    "module_report_memory", {"narrative_id": nid}
                )
                result.instance_links_count += await db_client.delete(
                    "instance_narrative_links", {"narrative_id": nid}
                )
                result.narratives_count += await db_client.delete("narratives", {"narrative_id": nid})
            for eid in event_ids:
                result.event_stream_count += await db_client.delete("event_stream", {"event_id": eid})
            for iid in chat_instance_ids:
                result.chat_memory_count += await db_client.delete(
                    "instance_json_format_memory_chat", {"instance_id": iid}
                )
                result.chat_instances_count += await db_client.delete(
                    "module_instances", {"instance_id": iid}
                )
            result.agent_messages_count += await db_client.delete(
                "agent_messages", {"agent_id": agent_id}
            )

            # IM conversation history, in the inbox's own tables. Scoped by
            # `agent_id` — NOT `owner_user_id`, since one user may own several
            # agents and each has its own threads.
            #
            # What prevents orphans is the TRANSACTION this whole block runs in,
            # not the order of these two deletes: the thread_id set is read once,
            # before either, so both see the same list either way. An earlier
            # version of this comment claimed messages-first was load-bearing —
            # mutation-checked and it is not, swapping them leaves the suite green.
            # The order that WOULD matter is moving the read between the deletes,
            # which is the one edit that comment's reasoning would have permitted.
            threads = await db_client.get(
                "inbox_threads", filters={"agent_id": agent_id}
            )
            for tid in {t["thread_id"] for t in threads if t.get("thread_id")}:
                result.inbox_thread_messages_count += await db_client.delete(
                    "inbox_thread_messages", {"thread_id": tid}
                )
            result.inbox_threads_count += await db_client.delete(
                "inbox_threads", {"agent_id": agent_id}
            )

            # Legacy IM history: rows the retired `ChannelInboxWriter` left on
            # deployed databases. Only channels where this agent is the SOLE
            # member (its private DMs) are wiped; shared channels are left for
            # the other members. Retire this branch when those rows are purged.
            member_rows = await db_client.get(
                "bus_channel_members", filters={"agent_id": agent_id}
            )
            for cid in {r["channel_id"] for r in member_rows if r.get("channel_id")}:
                others = await db_client.get("bus_channel_members", filters={"channel_id": cid})
                if len(others) <= 1:
                    result.bus_messages_count += await db_client.delete(
                        "bus_messages", {"channel_id": cid}
                    )
            result.bus_failures_count += await db_client.delete(
                "bus_message_failures", {"agent_id": agent_id}
            )

        if clear_memory:
            for kind in MEMORY_KINDS:
                result.memory_rows_count += await db_client.delete(
                    f"memory_{kind}", {"agent_id": agent_id}
                )
            result.memory_rows_count += await db_client.delete(
                "memory_consolidation_queue", {"agent_id": agent_id}
            )
            result.artifacts_count += await db_client.delete(
                "instance_artifacts", {"agent_id": agent_id}
            )

    # ---- Disk deletes (best-effort, after commit) ----
    if clear_conversations:
        try:
            result.disk_markdown_removed = NarrativeMarkdownManager(
                agent_id, user_id, base_path=narrative_base
            ).delete_all()
        except Exception as e:  # noqa: BLE001 — capture, never fail the wipe
            result.disk_errors.append(f"narratives: {e}")
            logger.warning(f"[wipe] failed to delete narratives dir: {e}")
        try:
            result.disk_trajectories_removed = TrajectoryRecorder(
                agent_id, user_id, base_path=trajectory_base
            ).delete_all()
        except Exception as e:  # noqa: BLE001
            result.disk_errors.append(f"trajectories: {e}")
            logger.warning(f"[wipe] failed to delete trajectories dir: {e}")
        try:
            svc = session_service or SessionService()
            result.session_removed = await svc.delete_session(agent_id, user_id)
        except Exception as e:  # noqa: BLE001
            result.disk_errors.append(f"session: {e}")
            logger.warning(f"[wipe] failed to delete session file: {e}")

    logger.info(
        f"[wipe] agent={agent_id} user={user_id} scopes={result.scopes} "
        f"narratives={result.narratives_count} events={result.events_count} "
        f"bus_messages={result.bus_messages_count} "
        f"memory_rows={result.memory_rows_count} disk_errors={len(result.disk_errors)}"
    )
    return result
