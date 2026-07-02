"""
@file_name: me.py
@author:
@date: 2026-06-23
@description: Owner-scoped ("/api/me") read endpoints powering the "You"
workspace — data aggregated ACROSS all of the current user's agents, as
opposed to the per-agent endpoints under /api/agents/{agent_id}.

First endpoint: GET /api/me/narratives — every narrative belonging to any
agent the user owns, flattened for the Narra Memory timeline (one storyline
per narrative: title, topic, owning agent, summary, activity, and the
created→updated span that becomes its bar on the time axis).

Read-only. Identity comes strictly from auth_middleware via
resolve_current_user_id (never a client-supplied id).
"""
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api

router = APIRouter()


def _loads(value: Any, fallback: Any) -> Any:
    """Best-effort JSON decode for a TEXT/JSON column; never raises."""
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


@router.get("/narratives")
async def get_my_narratives(
    request: Request,
    limit: int = Query(300, ge=1, le=1000),
    include_default: bool = Query(
        False,
        description="Include the seeded scaffold narratives (is_special="
        "'default', e.g. GreetingAndCourtesy). Off by default: those are "
        "routing buckets created at agent setup, not lived storylines.",
    ),
):
    """
    All narratives across every agent the user owns — the owner-level
    Narra Memory timeline source.

    Each item is one storyline:
      narrative_id, agent_id, agent_name, type, is_special,
      name, summary, topic_hint, topic_keywords[],
      round_counter, created_at, updated_at
    """
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()

    # Single indexed join (agents.created_by, narratives.agent_id). LIMIT is an
    # int clamped by Query(ge/le), so it is safe to inline (avoids driver
    # quirks with parameterised LIMIT across SQLite/MySQL).
    default_filter = "" if include_default else "AND n.is_special != 'default'"
    rows = await db.execute(
        f"""
        SELECT
            n.narrative_id    AS narrative_id,
            n.agent_id        AS agent_id,
            a.agent_name      AS agent_name,
            n.type            AS type,
            n.is_special      AS is_special,
            n.narrative_info  AS narrative_info,
            n.topic_hint      AS topic_hint,
            n.topic_keywords  AS topic_keywords,
            n.round_counter   AS round_counter,
            n.created_at      AS created_at,
            n.updated_at      AS updated_at
        FROM narratives n
        JOIN agents a ON n.agent_id = a.agent_id
        WHERE a.created_by = %s
        {default_filter}
        ORDER BY n.updated_at DESC
        LIMIT {int(limit)}
        """,
        (user_id,),
    )

    narratives: List[Dict[str, Any]] = []
    for row in rows or []:
        info = _loads(row.get("narrative_info"), {})
        name = (info.get("name") or "").strip()
        summary = (info.get("current_summary") or info.get("description") or "").strip()
        narratives.append(
            {
                "narrative_id": row.get("narrative_id"),
                "agent_id": row.get("agent_id"),
                "agent_name": row.get("agent_name") or row.get("agent_id"),
                "type": row.get("type"),
                "is_special": row.get("is_special"),
                "name": name,
                "summary": summary,
                "topic_hint": row.get("topic_hint") or "",
                "topic_keywords": _loads(row.get("topic_keywords"), []),
                "round_counter": row.get("round_counter") or 0,
                "created_at": format_for_api(row.get("created_at")),
                "updated_at": format_for_api(row.get("updated_at")),
            }
        )

    logger.debug(
        f"/api/me/narratives: user={user_id} -> {len(narratives)} narratives"
    )
    return {"success": True, "narratives": narratives}


def _entity_key(etype: str, attrs: Dict[str, Any]) -> str:
    """Merge key for the SAME real-world entity seen by several agents.

    Keyed on entity_type + a normalised identity (entity_name, else entity_id),
    so e.g. the user "kz" known by three agents collapses to one node.
    """
    ident = (attrs.get("entity_name") or attrs.get("entity_id") or "").strip().lower()
    return f"{etype}:{ident}"


@router.get("/network")
async def get_my_network(request: Request, limit: int = Query(500, ge=1, le=2000)):
    """
    The owner-level Nexus Network: every entity any of the user's agents knows
    (people / agents / groups), MERGED across agents into one graph.

    Live social entities now live in `memory_entity` (kind='entity'); the old
    `instance_social_entities` table is tombstoned. Each row is one agent's view
    of one entity; here they are deduped (same entity seen by N agents → one
    node carrying `known_by`).

    Returns: { you, entities: [{ key, name, type, familiarity, strength,
    interactions, last_interaction_time, description, expertise_domains[],
    known_by: [agent_name...] }] }
    """
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()

    rows = await db.execute(
        f"""
        SELECT
            me.agent_id      AS agent_id,
            a.agent_name     AS agent_name,
            me.subtype       AS subtype,
            me.attributes    AS attributes
        FROM memory_entity me
        JOIN agents a ON me.agent_id = a.agent_id
        WHERE a.created_by = %s
          AND me.kind = 'entity'
          AND me.expired_at IS NULL
        LIMIT {int(limit)}
        """,
        (user_id,),
    )

    # Roster of the user's own agents — used to reconcile entity type: an
    # entity that IS one of your agents must read as 'agent' even when a peer
    # recorded it as a 'user' (the LLM mis-typed it, e.g. Boss seen as a user).
    agent_rows = await db.execute(
        "SELECT agent_name FROM agents WHERE created_by = %s", (user_id,)
    )
    agent_names = {
        (r.get("agent_name") or "").strip().lower()
        for r in (agent_rows or [])
        if r.get("agent_name")
    }

    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        attrs = _loads(row.get("attributes"), {})
        if not isinstance(attrs, dict):
            continue
        etype = (row.get("subtype") or attrs.get("entity_type") or "user").strip()
        name = (attrs.get("entity_name") or attrs.get("entity_id") or "").strip()
        if not name:
            continue
        # Reconcile against the real agent roster (before keying, so the same
        # entity merges regardless of how each agent typed it).
        if name.strip().lower() in agent_names:
            etype = "agent"
        key = _entity_key(etype, attrs)
        agent_name = row.get("agent_name") or row.get("agent_id")
        # Is this entity the owner themselves (the agents' record of "you")?
        # That node is the graph centre, not an outer node.
        eid = (attrs.get("entity_id") or "").strip()
        is_self = etype == "user" and (
            eid == user_id or name.lower() == (user_id or "").lower()
        )

        strength = float(attrs.get("relationship_strength") or 0.0)
        interactions = int(attrs.get("interaction_count") or 0)
        familiarity = (attrs.get("familiarity") or "known_of").strip()
        last_seen = attrs.get("last_interaction_time")
        desc = (attrs.get("entity_description") or attrs.get("persona") or "").strip()
        expertise = attrs.get("expertise_domains") or []

        node = merged.get(key)
        if node is None:
            merged[key] = {
                "key": key,
                "name": name,
                "type": etype,
                "is_self": is_self,
                "familiarity": familiarity,
                "strength": strength,
                "interactions": interactions,
                "last_interaction_time": format_for_api(last_seen),
                "description": desc,
                "expertise_domains": list(expertise) if isinstance(expertise, list) else [],
                "known_by": [agent_name],
            }
        else:
            # 'direct' beats 'known_of'; strength = best; interactions accumulate.
            if is_self:
                node["is_self"] = True
            if familiarity == "direct":
                node["familiarity"] = "direct"
            node["strength"] = max(node["strength"], strength)
            node["interactions"] += interactions
            if len(desc) > len(node["description"]):
                node["description"] = desc
            if agent_name not in node["known_by"]:
                node["known_by"].append(agent_name)
            for e in expertise if isinstance(expertise, list) else []:
                if e not in node["expertise_domains"]:
                    node["expertise_domains"].append(e)

    entities = sorted(
        merged.values(),
        key=lambda n: (len(n["known_by"]), n["strength"], n["interactions"]),
        reverse=True,
    )

    logger.debug(
        f"/api/me/network: user={user_id} -> {len(rows or [])} rows, "
        f"{len(entities)} merged entities"
    )
    return {"success": True, "entities": entities}


@router.get("/worldview")
async def get_my_worldview(request: Request, world_per_agent: int = Query(3, ge=0, le=8)):
    """
    Worldview: how each of the user's agents sees the user, and a glimpse of
    each agent's own worldview — the two halves that compose "your world."

    Per agent (one lens):
      - sees_you: that agent's `persona` for the user (its characterisation of
        you), from its `memory_entity` record where entity_id == the user;
        falls back to the first line of entity_description.
      - worldview: a few of that agent's world observations (memory_observation
        subtype='world'), highest-salience first — its own model of the world.

    Only agents that actually hold a view of the user appear.
    """
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()

    agent_rows = await db.execute(
        "SELECT agent_id, agent_name FROM agents WHERE created_by = %s", (user_id,)
    )
    name_by_id = {
        r.get("agent_id"): (r.get("agent_name") or r.get("agent_id"))
        for r in (agent_rows or [])
    }

    # Each agent's record OF the user → its persona/description of you.
    ent_rows = await db.execute(
        """
        SELECT me.agent_id AS agent_id, me.attributes AS attributes
        FROM memory_entity me
        JOIN agents a ON me.agent_id = a.agent_id
        WHERE a.created_by = %s AND me.kind = 'entity' AND me.expired_at IS NULL
        """,
        (user_id,),
    )
    view_by_agent: Dict[str, str] = {}
    for r in ent_rows or []:
        attrs = _loads(r.get("attributes"), {})
        if not isinstance(attrs, dict):
            continue
        if (attrs.get("entity_id") or "").strip() != user_id:
            continue  # strictly the agent's record of YOU
        persona = (attrs.get("persona") or "").strip()
        desc = (attrs.get("entity_description") or "").strip()
        view = persona or (desc.split("\n")[0] if desc else "")
        if view:
            view_by_agent[r.get("agent_id")] = view

    # Each agent's world observations (its own worldview).
    obs_rows = await db.execute(
        """
        SELECT mo.agent_id AS agent_id, mo.content_text AS content_text
        FROM memory_observation mo
        JOIN agents a ON mo.agent_id = a.agent_id
        WHERE a.created_by = %s AND mo.subtype = 'world' AND mo.expired_at IS NULL
        ORDER BY mo.salience DESC, mo.updated_at DESC
        """,
        (user_id,),
    )
    world_by_agent: Dict[str, List[str]] = {}
    for r in obs_rows or []:
        aid = r.get("agent_id")
        text = (r.get("content_text") or "").strip()
        if not text:
            continue
        bucket = world_by_agent.setdefault(aid, [])
        if len(bucket) < world_per_agent:
            bucket.append(text)

    lenses = []
    for aid, view in view_by_agent.items():
        lenses.append(
            {
                "agent_id": aid,
                "agent_name": name_by_id.get(aid, aid),
                "sees_you": view,
                "worldview": world_by_agent.get(aid, []),
            }
        )
    # Most-expressive lenses first (those with a worldview to show).
    lenses.sort(key=lambda l: (len(l["worldview"]), len(l["sees_you"])), reverse=True)

    logger.debug(f"/api/me/worldview: user={user_id} -> {len(lenses)} lenses")
    return {"success": True, "lenses": lenses}
