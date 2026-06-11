"""
@file_name: specs.py
@author: NetMind.AI
@date: 2026-06-03
@description: The per-kind MemoryKindSpec registry (the POLICY layer).

One place that declares, for every memory kind, how it dedups / merges / ranks
/ renders / consolidates. The MemoryEngine (mechanism) reads these; nothing
here is an algorithm. Importing this module registers all kinds — it is
imported once at memory-system bootstrap.

Kinds (design §4.3):
    event     raw turn trace (provenance)          append-only, feeds narrative
    chat      one conversation message             append-only, recency recall
    bus       inter-agent message                  append-only
    narrative one session-thread summary           consolidates (events→summary)
    entity    social entity profile                dedup+merge, consolidates
    job_memo  a job's memory side                  dedup by job, recency
    observation distilled world/experience belief  consolidates (the learning layer)
"""
from __future__ import annotations

from typing import List

from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.memory.spec import MemoryKindSpec, RecallWeights, register_spec
from xyz_agent_context.utils.timezone import utc_now

# ── shared policy helpers ────────────────────────────────────────────────────


def _name_key(r: MemoryRecord) -> str:
    """Entity dedup key — the entity's name (falls back to content)."""
    return (r.attributes or {}).get("name") or r.content_text


def _entity_merge(existing: MemoryRecord, incoming: MemoryRecord) -> MemoryRecord:
    """Entity merge APPENDS to the description rather than replacing it — a
    profile accretes facts over time (graphiti/social范式). Structured
    attributes shallow-merge; evidence and provenance accumulate."""
    now = utc_now()
    new_desc = (incoming.content_text or "").strip()
    if new_desc and new_desc not in (existing.content_text or ""):
        existing.history.append({"text": existing.content_text, "changed_at": now.isoformat()})
        existing.content_text = f"{existing.content_text}\n- {new_desc}".lstrip("\n- ") if existing.content_text else new_desc
    existing.tags = list(dict.fromkeys(existing.tags + incoming.tags))
    existing.source_ids = list(dict.fromkeys(existing.source_ids + incoming.source_ids))
    existing.attributes = {**existing.attributes, **incoming.attributes}
    existing.proof_count += max(1, incoming.proof_count)
    existing.updated_at = now
    return existing


def _bullets(header: str, records: List[MemoryRecord]) -> str:
    if not records:
        return ""
    lines = "\n".join(f"- {r.content_text}" for r in records if r.content_text)
    return f"{header}\n{lines}" if lines else ""


# ── prompts (kept tight; the consolidation default lives in consolidate.py) ──

_ENTITY_DEDUP_PROMPT = """\
You are deduplicating people/agents/groups an AI agent knows. Given a NEW
entity description and numbered CANDIDATES, decide whether the NEW one refers
to the SAME real entity as one candidate. Return that candidate's index, or
null if none match. When uncertain, return null — a false merge (two people
collapsed) is worse than keeping them separate."""

_OBSERVATION_EXTRACT_PROMPT = """\
Extract durable memory items from the latest interaction, as discrete facts.
Two kinds only:
- WORLD facts: objective statements about other people / the world
  ("Alice works at Google", "the deadline is June 6").
- EXPERIENCE facts: the agent's own actions / judgments / outcomes
  ("I recommended Python to Bob", "this user prefers terse replies").
Skip transient chit-chat. Each fact: one clear sentence, third person for
WORLD, first person for EXPERIENCE. Do NOT infer or compute across facts."""


# ── registrations ────────────────────────────────────────────────────────────

register_spec(MemoryKindSpec(
    kind="event",
    default_scope="narrative",
    recall=RecallWeights(recency=0.7, proof=0.0, salience=0.3),
    render=lambda rs: _bullets("Recent activity:", rs[:6]),
))

# chat kind retired (unified-memory overhaul P2): conversation search is now
# the per-interaction "event" index (chat+event merged, design §5). The
# memory_chat table is kept (migration still references it) but no longer
# registered as a searchable kind.

register_spec(MemoryKindSpec(
    kind="bus",
    default_scope="agent",
    recall=RecallWeights(recency=0.8, proof=0.0, salience=0.2),
    render=lambda rs: _bullets("Messages from other agents:", rs),
))

register_spec(MemoryKindSpec(
    kind="narrative",
    passive=True,
    default_scope="agent",
    consolidates=True,
    consolidate_threshold=4,
    recall=RecallWeights(recency=0.4, proof=0.2, salience=0.4),
    render=lambda rs: _bullets("Related threads:", rs),
))

register_spec(MemoryKindSpec(
    kind="entity",
    passive=True,
    default_scope="agent",
    subtypes=("user", "agent", "group"),
    dedup_key=_name_key,
    merge=_entity_merge,
    dedup_prompt=_ENTITY_DEDUP_PROMPT,
    consolidates=True,
    consolidate_threshold=6,
    recall=RecallWeights(recency=0.3, proof=0.4, salience=0.3),
    render=lambda rs: _bullets("People you know:", rs),
))

register_spec(MemoryKindSpec(
    kind="job",
    default_scope="narrative",
    dedup_key=lambda r: (r.attributes or {}).get("job_id", ""),
    recall=RecallWeights(recency=0.5, proof=0.1, salience=0.4),
    render=lambda rs: _bullets("Active tasks:", rs),
))

register_spec(MemoryKindSpec(
    kind="observation",
    passive=True,
    default_scope="agent",
    subtypes=("world", "experience"),
    extract_prompt=_OBSERVATION_EXTRACT_PROMPT,
    consolidates=True,
    consolidate_threshold=4,
    recall=RecallWeights(recency=0.3, proof=0.4, salience=0.3),
    render=lambda rs: _bullets("Relevant memories:", rs),
))
