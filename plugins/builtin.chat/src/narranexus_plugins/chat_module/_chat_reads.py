"""
@file_name: _chat_reads.py
@author:
@date: 2026-08-10
@description: The shared, dialect-safe implementation of the get_chat_history
tool — the single source of truth behind the AgentDataStore seam.

Hoisted out of _chat_mcp_tools.py so DirectStore (local) and the backend twin
route (cloud) both call THIS one function and stay byte-identical, letting the
cloud mcp container serve chat history without db credentials.

Two things change from the pre-seam tool, both deliberate:

1. **De-raw (rule #6).** The old tool ran a MySQL-only ``information_schema``
   existence check and raw ``SELECT `memory` FROM `{table}```; both broke on
   sqlite. ``instance_json_format_memory_chat`` is registered in schema_registry,
   so auto_migrate guarantees it exists — the existence check is dropped, and the
   read is ``db.get_one`` (dialect-safe on both backends; precedent:
   backend/routes/agents/chat_history.py get_simple_chat_history).

2. **Instance scoping (closes an IDOR).** The old tool took only ``instance_id``
   and returned that instance's chat to ANY caller — an agent could read another
   agent's conversation by guessing a ``chat_xxx`` id. It now takes the calling
   ``agent_id`` (LLM-passed, exactly like the sibling owner-facing tools)
   and the instance must belong to that agent; a foreign/unknown instance reads as
   an EMPTY history (no existence oracle — indistinguishable from an own instance
   that simply has no messages). The seam route additionally owner-gates agent_id,
   so a cloud caller must own the agent whose history it asks for.

   Residual surface, by design (do NOT hang more sensitive reads on this pattern
   without tightening it): agent_id is LLM-self-reported and the DirectStore
   (local/DMG) path has no gate, so locally this only narrows the attack from
   "guess an instance_id" to "guess a matching (agent_id, instance_id)" — strictly
   better, not closed. On the cloud path assert_owned closes CROSS-USER reads, but
   one owner reading their OWN agent A's history from agent B is still possible and
   is accepted (an owner already has full access to all their agents' data).
"""
from __future__ import annotations

import json

from loguru import logger

_CHAT_MEMORY_TABLE = "instance_json_format_memory_chat"


def _empty(instance_id: str, note: str) -> dict:
    return {
        "success": True,
        "instance_id": instance_id,
        "total_messages": 0,
        "messages": [],
        "note": note,
    }


async def fetch_chat_history(db, agent_id: str, instance_id: str, limit: int = 20) -> dict:
    """Return a Chat Instance's message history — the shared body of the
    get_chat_history tool. Instance-scoped to ``agent_id``; returns the tool's
    exact dict shape. Never raises (every failure is an in-band dict)."""
    try:
        # Scope: the instance must belong to the calling agent. A foreign or
        # unknown instance reads as empty history — no existence oracle.
        inst = await db.get_one("module_instances", {"instance_id": instance_id})
        if not inst or inst.get("agent_id") != agent_id:
            return _empty(instance_id, "This Chat Instance has no chat history yet")

        row = await db.get_one(_CHAT_MEMORY_TABLE, {"instance_id": instance_id})
        if not row or not row.get("memory"):
            return _empty(instance_id, "This Chat Instance has no chat history yet")

        try:
            memory_data = json.loads(row["memory"])
        except json.JSONDecodeError as e:
            logger.exception(f"ChatModule.get_chat_history: JSON parsing failed - {e}")
            return {
                "success": False,
                "instance_id": instance_id,
                "error": f"Chat history data format error: {str(e)}",
                "total_messages": 0,
                "messages": [],
            }

        messages = memory_data.get("messages", [])
        total_messages = len(messages)
        # limit <= 0 means "all"; otherwise return the most recent `limit`.
        if limit > 0 and total_messages > limit:
            messages = messages[-limit:]

        formatted_messages = []
        for msg in messages:
            formatted_msg = {
                "role": msg.get("role", "unknown"),
                "content": msg.get("content", ""),
            }
            if "meta_data" in msg:
                meta = msg["meta_data"]
                if "timestamp" in meta:
                    formatted_msg["timestamp"] = meta["timestamp"]
                if "event_id" in meta:
                    formatted_msg["event_id"] = meta["event_id"]
            formatted_messages.append(formatted_msg)

        return {
            "success": True,
            "instance_id": instance_id,
            "total_messages": total_messages,
            "returned_messages": len(formatted_messages),
            "messages": formatted_messages,
        }
    except Exception as e:  # noqa: BLE001 — in-band failure, never an exception
        logger.exception(f"ChatModule.get_chat_history: Query failed - {e}")
        return {
            "success": False,
            "instance_id": instance_id,
            "error": f"Query failed: {str(e)}",
            "total_messages": 0,
            "messages": [],
        }
