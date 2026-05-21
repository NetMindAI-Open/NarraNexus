"""
@file_name: _basic_info_mcp_tools.py
@author: Bin Liang
@date: 2026-05-20
@description: MCP server + narrative-awareness tools for BasicInfoModule (Fix #2 P3).

The agent's conversation history (built by ChatModule.hook_data_gathering) is a
single time-sorted timeline merging the current thread (full) with the latest
~30 lines across the user's OTHER threads, each line tagged
`[<time> · <topic> · nar=<narrative_id> · evt=<event_id>]`, plus a separate
"recent background activity" list (each with an evt= id). The system PICKS a
default narrative for the turn, but that pick isn't always right. These four
tools give the agent visibility + agency over it:

  - view_narrative(narrative_id): full info on a thread incl. ALL its chat
    history (the timeline only shows the latest trimmed slice).
  - view_event(event_id): one past turn's full agent-loop / reasoning detail
    (the timeline only carries the message that was sent to the user).
  - switch_narrative(narrative_id): declare that THIS turn belongs to that
    existing thread — the runtime re-attributes this turn's event + memory.
  - create_narrative(title, description): declare THIS turn starts a NEW thread.

switch/create are SIGNALS. The tool validates/creates and returns the target id;
the agent_runtime hook (step_4_persist_results) detects the call and does the
actual re-attribution. The tool process and the runtime are different processes,
so detection-from-the-tool-call is how they communicate (no shared state).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.utils.db_factory import get_db_client


# Tool names the runtime hook scans agent_loop_response for (keep in lockstep
# with step_4_persist_results._detect_narrative_routing_signal).
SWITCH_NARRATIVE_TOOL = "switch_narrative"
CREATE_NARRATIVE_TOOL = "create_narrative"


def create_basic_info_mcp_server(port: int) -> FastMCP:
    """Create the BasicInfoModule MCP server with the narrative tools."""
    mcp = FastMCP("basic_info_module")
    mcp.settings.port = port
    _register_narrative_tools(mcp)
    logger.info(
        f"BasicInfo MCP: narrative tools registered on port {port} "
        f"(view_narrative, view_event, {SWITCH_NARRATIVE_TOOL}, {CREATE_NARRATIVE_TOOL})"
    )
    return mcp


def _parse_info(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


async def _narrative_chat_history(db, narrative_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Pull the full chat history of a narrative from its ChatModule instances."""
    rows = await db.execute(
        "SELECT instance_id FROM instance_narrative_links WHERE narrative_id=%s",
        params=(narrative_id,), fetch=True,
    )
    inst_ids = [dict(r).get("instance_id") for r in (rows or [])]
    inst_ids = [i for i in inst_ids if i and i.startswith("chat_")]
    messages: List[Dict[str, Any]] = []
    for iid in inst_ids:
        mrows = await db.execute(
            "SELECT memory FROM instance_json_format_memory_chat WHERE instance_id=%s",
            params=(iid,), fetch=True,
        )
        if not mrows:
            continue
        mem = _parse_info(dict(mrows[0]).get("memory"))
        for m in mem.get("messages", []):
            meta = m.get("meta_data", {}) or {}
            messages.append({
                "time": str(meta.get("timestamp", ""))[:19],
                "role": m.get("role"),
                "content": (m.get("content") or "")[:2000],
                "event_id": meta.get("event_id"),
            })
    messages.sort(key=lambda x: x.get("time", ""))
    return messages[-limit:]


def _register_narrative_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="view_narrative",
        description=(
            "Look up one conversation thread (narrative) IN FULL by its id — "
            "including its entire chat history. Your conversation history is a "
            "merged, time-trimmed timeline (latest ~30 lines across threads), so "
            "an older thread may be partly cut off. Take a narrative_id from any "
            "message tag `[.. nar=<id> ..]` and pass it here to read that whole "
            "thread before deciding how to respond. Returns {name, description, "
            "summary, keywords, message_count, messages:[{time, role, content, "
            "event_id}]}."
        ),
    )
    async def view_narrative(agent_id: str, narrative_id: str) -> dict:
        try:
            db = await get_db_client()
            rows = await db.execute(
                "SELECT narrative_info, topic_keywords FROM narratives WHERE narrative_id=%s",
                params=(narrative_id,), fetch=True,
            )
            if not rows:
                return {"error": f"narrative {narrative_id} not found"}
            d = dict(rows[0])
            info = _parse_info(d.get("narrative_info"))
            kws = d.get("topic_keywords")
            history = await _narrative_chat_history(db, narrative_id)
            logger.info(f"[NarrativeTool] view_narrative({narrative_id}) -> {len(history)} messages")
            return {
                "narrative_id": narrative_id,
                "name": info.get("name"),
                "description": info.get("description"),
                "summary": info.get("current_summary"),
                "keywords": kws if isinstance(kws, list) else _parse_info(kws) or kws,
                "message_count": len(history),
                "messages": history,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NarrativeTool] view_narrative failed: {e}")
            return {"error": str(e)}

    @mcp.tool(
        name="view_event",
        description=(
            "Get one past turn's FULL detail by its event id (the `evt=<id>` in "
            "a timeline message tag or a recent-activity line). The timeline only "
            "shows the message you SENT; this returns that turn's full agent-loop "
            "trace and reasoning. Returns {final_output, trigger, time, event_log}."
        ),
    )
    async def view_event(agent_id: str, event_id: str) -> dict:
        try:
            db = await get_db_client()
            rows = await db.execute(
                "SELECT `trigger`, trigger_source, env_context, final_output, event_log, created_at, narrative_id "
                "FROM events WHERE event_id=%s",
                params=(event_id,), fetch=True,
            )
            if not rows:
                return {"error": f"event {event_id} not found"}
            d = dict(rows[0])
            log_raw = d.get("event_log")
            if isinstance(log_raw, (bytes, bytearray)):
                log_raw = log_raw.decode("utf-8", errors="replace")
            logger.info(f"[NarrativeTool] view_event({event_id})")
            return {
                "event_id": event_id,
                "narrative_id": d.get("narrative_id"),
                "trigger": d.get("trigger"),
                "trigger_source": d.get("trigger_source"),
                "time": str(d.get("created_at", ""))[:19],
                "input": _parse_info(d.get("env_context")).get("input"),
                "final_output": (d.get("final_output") or "")[:8000],
                "event_log": (log_raw or "")[:20000],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NarrativeTool] view_event failed: {e}")
            return {"error": str(e)}

    @mcp.tool(
        name=SWITCH_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn belongs to a DIFFERENT existing thread than "
            "the one the system defaulted to. Use it when the user's message "
            "(especially a short reply like '好'/'yes') is actually continuing "
            "another thread shown in your timeline — pass that thread's "
            "narrative_id (from its `[.. nar=<id> ..]` tag). The system will "
            "re-file this turn into that thread so future context stays correct. "
            "Call this BEFORE you reply. If none fits and it's a new topic, use "
            "create_narrative instead."
        ),
    )
    async def switch_narrative(agent_id: str, narrative_id: str) -> dict:
        try:
            db = await get_db_client()
            rows = await db.execute(
                "SELECT 1 FROM narratives WHERE narrative_id=%s",
                params=(narrative_id,), fetch=True,
            )
            if not rows:
                return {"ok": False, "error": f"narrative {narrative_id} not found"}
            logger.info(f"[NarrativeTool] switch_narrative -> {narrative_id} (agent={agent_id})")
            return {
                "ok": True,
                "narrative_id": narrative_id,
                "message": "This turn will be attributed to this narrative.",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NarrativeTool] switch_narrative failed: {e}")
            return {"ok": False, "error": str(e)}

    @mcp.tool(
        name=CREATE_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn starts a brand-NEW conversation thread "
            "(topic) — use it when the user's message doesn't belong to any "
            "thread in your timeline. Provide a short title and one-line "
            "description. The system creates the thread and files this turn into "
            "it. Call this BEFORE you reply. Returns {narrative_id}."
        ),
    )
    async def create_narrative(agent_id: str, user_id: str, title: str, description: str = "") -> dict:
        # SIGNAL only: the runtime hook (step_4) reads {title, description} from
        # this call, CREATES the narrative, and files this turn into it. We do
        # not create here so the tool process and runtime don't double-create.
        if not (title or "").strip():
            return {"ok": False, "error": "title is required"}
        logger.info(f"[NarrativeTool] create_narrative signal: title={title!r} (agent={agent_id})")
        return {
            "ok": True,
            "title": title,
            "message": "Noted — a new narrative with this title will be created and this turn filed into it.",
        }
