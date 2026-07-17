"""
@file_name: id_field_map.py
@author: NetMind.AI
@date: 2026-05-08
@description: Structured field registry for ID rewrite (PRD §8.11 Layer 2)

Each table → {column_or_jsonpath: id_kind}.
- "col_name"            ← direct column substitution
- "col_name[*]"         ← JSON list of IDs; substitute each element
- "col_name.subkey"     ← JSON object's subkey
- "col_name[*].subkey"  ← list of objects, substitute each element's subkey
"""

import secrets
from typing import Dict


STRUCTURED_ID_FIELDS: Dict[str, Dict[str, str]] = {
    "agents": {"agent_id": "agent"},
    "events": {
        "event_id": "event",
        "agent_id": "agent",
        "narrative_id": "narrative",
    },
    "narratives": {
        "narrative_id": "narrative",
        "agent_id": "agent",
        "main_chat_instance_id": "instance",
    },
    "agent_messages": {
        "message_id": "message",
        "agent_id": "agent",
        "narrative_id": "narrative",
        "event_id": "event",
    },
    "module_instances": {
        "instance_id": "instance",
        "agent_id": "agent",
    },
    # Bundle-internal key for the social-entities export (social_entities.json).
    # Entities live in memory_entity now, but the bundle still carries the same
    # flat per-entity records; this names the id-bearing fields for rewrite/scrub.
    "social_entities": {
        "instance_id": "instance",
    },
    "instance_jobs": {
        "instance_id": "instance",
        "job_id": "job",
        "agent_id": "agent",
        "narrative_id": "narrative",
        # related_entity_id mostly references a social entity which is often an
        # agent_id within the same closure; treat as agent for v1. Polymorphic
        # case (entity_type != 'agent') is left intact via fallback regex pass.
        "related_entity_id": "agent",
    },
    "instance_narrative_links": {
        "instance_id": "instance",
        "narrative_id": "narrative",
    },
    "instance_awareness": {
        "instance_id": "instance",
    },
    "instance_module_report_memory": {
        "instance_id": "instance",
    },
    "instance_json_format_memory": {
        "instance_id": "instance",
    },
    "instance_json_format_memory_chat": {
        "instance_id": "instance",
    },
    "module_report_memory": {
        # No instance_id on this legacy table — keyed by (narrative_id, module_name)
        "narrative_id": "narrative",
    },
    "bus_channels": {
        "channel_id": "channel",
        # `created_by` stores an AGENT_ID (the channel owner agent), not a
        # user_id — see local_bus.create_channel:
        #     created_by = members[0] if members else "system"
        # Declaring it here makes import's rewrite_row map it old → new agent_id
        # via id_map, instead of falling into importer's user-attribution loop
        # which would force-overwrite it with the recipient user_id. That force-
        # overwrite was breaking trigger's "channel owner always activated"
        # semantics on the receiving side (msg_bus_trigger.py:154 compares
        # created_by against an agent_id).
        "created_by": "agent",
    },
    "bus_channel_members": {
        "channel_id": "channel",
        "agent_id": "agent",
    },
    "bus_messages": {
        "message_id": "message",
        "channel_id": "channel",
    },
    "bus_agent_registry": {
        "agent_id": "agent",
    },
    "teams": {
        "team_id": "team",
    },
    "team_members": {
        "team_id": "team",
        "agent_id": "agent",
    },
    "instance_artifacts": {
        "artifact_id": "artifact",
        "agent_id": "agent",
    },
    "mcp_urls": {
        "mcp_id": "mcp",
        "agent_id": "agent",
    },
    # IM channel credentials (opt-in export). Only agent_id is an internal ID;
    # it must map old → new so the imported credential attaches to the freshly
    # minted agent instead of dangling at the source agent_id. Everything else
    # on these tables (app_id, tokens, owner_user_id, bot_user_id, …) is
    # IM-namespace and is preserved verbatim. Kept in sync with
    # bundle/channel_credential_tables.py::CHANNEL_CREDENTIAL_TABLES.
    "lark_credentials": {"agent_id": "agent"},
    "channel_slack_credentials": {"agent_id": "agent"},
    "channel_telegram_credentials": {"agent_id": "agent"},
    "channel_discord_credentials": {"agent_id": "agent"},
    "channel_wechat_credentials": {"agent_id": "agent"},
    "channel_narramessenger_credentials": {"agent_id": "agent"},
}


# kind → prefix (for new ID generation).
ID_KIND_PREFIXES: Dict[str, str] = {
    "agent": "agent",
    "event": "evt",
    "narrative": "nar",
    "instance": "inst",
    "message": "msg",
    "job": "job",
    "team": "team",
    "channel": "ch",
    "mcp": "mcp",
    "artifact": "art",
}


def gen_new_id(kind: str) -> str:
    prefix = ID_KIND_PREFIXES.get(kind)
    if not prefix:
        raise ValueError(f"Unknown ID kind: {kind}")
    return f"{prefix}_{secrets.token_hex(6)}"
