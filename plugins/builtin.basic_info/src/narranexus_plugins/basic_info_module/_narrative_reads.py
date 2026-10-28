"""
@file_name: _narrative_reads.py
@author:
@date: 2026-08-10
@description: Dialect-safe narrative / event read helpers shared by the
AgentDataStore seam (DirectStore) and the backend narrative/event routes
(the HttpStore path).

The BasicInfoModule's view_narrative / view_event / switch_narrative MCP tools
used to hand-write MySQL (``SELECT `trigger` … FROM events``, ``information_
schema``-free but still raw ``%s`` + backtick), which only runs locally via the
sqlite translation shim and is exactly the kind of thing the dual-dialect rule
forbids leaking. These functions reimplement those reads on the
``AsyncDatabaseClient`` helpers (``get_one`` / ``get`` / ``get_by_ids``), which
are dialect-safe on SQLite AND MySQL.

Each ``fetch_*`` / ``check_*`` returns the COMPLETE result dict and never
raises — so the seam's DirectStore and the route can both just ``return await
fetch_x(...)`` and get byte-identical output (parity by a single shared
implementation, not two hand-kept copies). They also add the ``agent_id``
ownership filter the raw-SQL tools lacked: the old tools would return ANY
agent's narrative/event by id (a cross-tenant read); these scope to the caller.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from loguru import logger

from narranexus.platform.utils.timezone import (
    DEFAULT_TIMEZONE,
    format_timestamp_for_agent,
    resolve_timezone,
)

# Distinct bounds — same value today, unrelated meanings (a shared literal
# invites changing one and breaking the other).
_MAX_NARRATIVE_LINKS = 500   # link ROWS fetched (a narrative links many non-chat
                             # instances too, so this sits well above the cap below)
_MAX_CHAT_INSTANCES = 100    # ChatModule instances actually fanned out to
_MAX_MESSAGES = 200          # messages returned (most recent)


async def _agent_owner_timezone(db, agent_id: str) -> str:
    """The timezone the agent's owner reads times in.

    Needed because these functions back the `view_narrative` / `view_event`
    MCP tools, and the history timeline explicitly points the agent at them
    ("pass it to view_event() to fetch that turn's full detail"). Once the
    timeline started rendering in the user's frame with an explicit offset
    (2026-08-18), leaving these on a raw `[:19]` UTC slice would have handed
    the model two different dates for one event and told it to walk from the
    framed one to the bare one.

    The owner comes from `agents.created_by`. For the narrative path that is
    the only option — `narratives` has no user column. For the EVENT path it
    is a deliberate choice: `events` DOES carry `user_id`, and reading it
    would save two queries, but then `view_event` and `view_narrative` could
    resolve different zones for the same conversation and report dates a day
    apart for the same message. Both views must share the frame the history
    timeline uses, and the timeline keys off the turn owner. Please do not
    "optimise" the event path onto `row["user_id"]`.

    Fail-open to UTC — a view that renders beats a view that raises, and
    these helpers promise never to raise.
    """
    try:
        from narranexus.platform.repository import UserRepository

        row = await db.get_one("agents", filters={"agent_id": agent_id})
        owner = (row or {}).get("created_by")
        if not owner:
            return DEFAULT_TIMEZONE
        return resolve_timezone(await UserRepository(db).get_user_timezone(owner))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[narrative_reads] timezone lookup failed for {agent_id}: {e}")
        return DEFAULT_TIMEZONE


def _parse_info(raw: Any) -> Any:
    # Returns a dict for dict/JSON-object input, but a list for a JSON-array
    # string (the `keywords` path relies on that) — hence -> Any, not -> Dict.
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def narrative_chat_history(
    db,
    narrative_id: str,
    limit: int = _MAX_MESSAGES,
    user_tz: str = DEFAULT_TIMEZONE,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Recent chat history of a narrative from its ChatModule instances.

    Returns ``(messages, truncated)`` — truncated is True when the chat-instance
    fan-out cap was hit, so the caller can surface that an older tail is missing
    rather than dropping it silently (rule #16). Newest links first, THEN filter
    to chat instances: the links table carries non-chat instances too, so
    truncating before the prefix filter would silently drop chat history when the
    newest rows are mostly non-chat.
    """
    links = await db.get(
        "instance_narrative_links",
        filters={"narrative_id": narrative_id},
        limit=_MAX_NARRATIVE_LINKS,
        order_by="created_at DESC",
    )
    chat_ids = [
        row.get("instance_id")
        for row in (links or [])
        if (row.get("instance_id") or "").startswith("chat_")
    ]
    truncated = len(chat_ids) > _MAX_CHAT_INSTANCES
    inst_ids = chat_ids[:_MAX_CHAT_INSTANCES]

    messages: List[Dict[str, Any]] = []
    mrows = await db.get_by_ids(
        "instance_json_format_memory_chat", "instance_id", inst_ids
    ) if inst_ids else []
    for mrow in mrows:
        # get_by_ids preserves order and pads MISSING ids with None. A chat_
        # instance can be linked (step_1) before its memory row exists (step_5),
        # so an interrupted first turn leaves a link with no memory — skip it,
        # don't crash the whole view (the old tool + chat_history route do too).
        if not mrow:
            continue
        mem = _parse_info(mrow.get("memory"))
        for m in mem.get("messages", []):
            meta = m.get("meta_data", {}) or {}
            raw_ts = str(meta.get("timestamp", ""))
            messages.append({
                # Rendered in the owner's frame with an explicit offset, so a
                # date read off view_narrative matches the one on the history
                # timeline for the same message.
                "time": format_timestamp_for_agent(raw_ts, user_tz),
                "role": m.get("role"),
                "content": (m.get("content") or "")[:2000],
                "event_id": meta.get("event_id"),
                # Sort key only — stripped below. Ordering must key off the
                # STORED UTC value: the rendered string starts with "??" for an
                # unparseable timestamp, which would sort such rows to the front
                # of the history instead of leaving them where they belong.
                "_sort_ts": raw_ts,
            })
    messages.sort(key=lambda x: x.get("_sort_ts", ""))
    for m in messages:
        m.pop("_sort_ts", None)
    # truncated must also cover the MESSAGE cap, not just the instance fan-out —
    # otherwise a narrative under the instance cap but over _MAX_MESSAGES would
    # silently drop the older tail with truncated=False (rule #16).
    return messages[-limit:], truncated or len(messages) > limit


async def fetch_narrative_view(db, agent_id: str, narrative_id: str) -> dict:
    """Full info on one narrative (thread) including its chat history — the
    de-raw'd, agent-scoped body behind view_narrative."""
    try:
        row = await db.get_one("narratives", filters={"narrative_id": narrative_id})
        if not row or row.get("agent_id") != agent_id:
            return {"success": False, "error": f"narrative {narrative_id} not found"}
        info = _parse_info(row.get("narrative_info"))
        kws = row.get("topic_keywords")
        keywords = kws if isinstance(kws, list) else (_parse_info(kws) or [])
        user_tz = await _agent_owner_timezone(db, agent_id)
        history, truncated = await narrative_chat_history(
            db, narrative_id, user_tz=user_tz
        )
        return {
            "success": True,
            "narrative_id": narrative_id,
            "name": info.get("name"),
            "description": info.get("description"),
            "summary": info.get("current_summary"),
            "keywords": keywords if isinstance(keywords, list) else [],
            "message_count": len(history),
            "messages": history,
            "truncated": truncated,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[narrative_reads] fetch_narrative_view failed: {e}")
        return {"success": False, "error": str(e)}


async def fetch_event_view(db, agent_id: str, event_id: str) -> dict:
    """One past turn's full detail by event id — the de-raw'd, agent-scoped body
    behind view_event. ``event_log`` is the RAW step trace string (truncated),
    NOT the frontend event-log route's parsed thinking/tool_calls."""
    try:
        row = await db.get_one("events", filters={"event_id": event_id, "agent_id": agent_id})
        if not row:
            return {"success": False, "error": f"event {event_id} not found"}
        log_raw = row.get("event_log")
        if isinstance(log_raw, (bytes, bytearray)):
            log_raw = log_raw.decode("utf-8", errors="replace")
        return {
            "success": True,
            "event_id": event_id,
            "narrative_id": row.get("narrative_id"),
            "trigger": row.get("trigger"),
            "trigger_source": row.get("trigger_source"),
            # Same frame as the history timeline: the timeline tag tells the
            # agent to call view_event for this very event, so the two must
            # not disagree about which day it happened.
            "time": format_timestamp_for_agent(
                row.get("created_at"), await _agent_owner_timezone(db, agent_id)
            ),
            "input": _parse_info(row.get("env_context")).get("input"),
            "final_output": (row.get("final_output") or "")[:8000],
            "event_log": (log_raw or "")[:20000],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[narrative_reads] fetch_event_view failed: {e}")
        return {"success": False, "error": str(e)}


async def check_narrative_switch(db, agent_id: str, narrative_id: str) -> dict:
    """Validate that ``narrative_id`` exists AND belongs to ``agent_id`` — the
    de-raw'd, agent-scoped body behind switch_narrative. A validation, not a
    re-attribution: the actual turn re-filing happens only inside a live agent
    run (step_4_persist_results), which neither this nor the tool can reach."""
    try:
        row = await db.get_one("narratives", filters={"narrative_id": narrative_id})
        if not row or row.get("agent_id") != agent_id:
            return {"success": False, "error": f"narrative {narrative_id} not found"}
        return {
            "success": True,
            "narrative_id": narrative_id,
            "message": "This turn will be attributed to this narrative.",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[narrative_reads] check_narrative_switch failed: {e}")
        return {"success": False, "error": str(e)}
