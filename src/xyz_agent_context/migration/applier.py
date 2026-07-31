"""
@file_name: applier.py
@author: NetMind.AI
@date: 2026-07-21
@description: Migration apply — execute a MigrationPlan onto a NarraNexus agent.

The write half of Agent Migration: given a `MigrationPlan` (from `mapper`),
create (or reuse) an agent and populate it — awareness, general memory, skills,
per-agent url-MCP, and one Narrative per imported session. Each session is
summarized by ONE helper_llm call, the Narrative is created + enriched + saved
directly (no agent loop, no embeddings), and its turns are retained as
`event` memory scoped to that Narrative (append-only; observation would be
consolidated away).

Faithful-reproduction skill policy (Owner): a skill with a `local_path` is
COPIED verbatim into the agent's workspace `skills/<name>/` (migration = copy the
original agent, not substitute a same-name marketplace skill). Only a skill with
no local source falls back to a marketplace install.

Reuses existing repos/factories/services (no HTTP coupling) — callable from the
route or any async context; `user_id` is passed in explicitly.
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.agent_framework.llm.helper_sdk import get_helper_sdk
from xyz_agent_context.migration.mapper import MigrationPlan, PlannedNarrative
from xyz_agent_context.memory import (
    MemoryEngine,
    MemoryRecord,
    SCOPE_AGENT,
    SCOPE_NARRATIVE,
)
from xyz_agent_context.narrative.models import DynamicSummaryEntry, NarrativeType
from xyz_agent_context.narrative.narrative_service import NarrativeService
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.repository.instance_repository import InstanceRepository
from xyz_agent_context.repository.instance_awareness_repository import (
    InstanceAwarenessRepository,
)
from xyz_agent_context.repository.mcp_repository import MCPRepository
from xyz_agent_context.repository.event_memory_repository import EventMemoryRepository
from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory
from xyz_agent_context.utils.workspace_paths import agent_workspace_path


class ApplyResult(BaseModel):
    agent_id: str
    created: bool
    awareness_written: bool = False
    memory_written: int = 0
    # Default NarraNexus skills (netmind-vision, officecli, ...) provisioned for
    # every new agent — same set a normally-created agent gets.
    default_skills_installed: List[str] = Field(default_factory=list)
    skills_copied: List[str] = Field(default_factory=list)
    skills_installed: List[str] = Field(default_factory=list)
    skills_unmatched: List[str] = Field(default_factory=list)
    mcp_added: List[str] = Field(default_factory=list)
    mcp_stdio_skipped: List[str] = Field(default_factory=list)
    # One Narrative created per imported session (title of each).
    narratives_created: List[str] = Field(default_factory=list)
    # Total imported conversation turns retained as event memory (per Narrative).
    memory_turns_retained: int = 0
    warnings: List[str] = Field(default_factory=list)


class _NarrativeSummary(BaseModel):
    """Structured output of the one-shot helper_llm summary of a source session."""

    description: str = ""      # 1-sentence: what this narrative/session is about
    current_summary: str = ""  # the rolled-up state/content of the conversation
    topic_hint: str = ""       # short routing hint
    topic_keywords: List[str] = Field(default_factory=list)
    dynamic_summary: List[str] = Field(default_factory=list)  # chronological milestones


_SUMMARIZE_INSTRUCTIONS = (
    "You are importing a past coding-assistant conversation into a long-memory "
    "agent. Read the transcript (it may start with an earlier-history summary) "
    "and produce: a one-sentence `description` of what the session was about; a "
    "`current_summary` capturing the state, decisions, and open threads; a short "
    "`topic_hint`; 3-8 `topic_keywords`; and `dynamic_summary`, a few chronological "
    "one-line milestones. Be faithful and concise; do not invent facts."
)


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


# How many recent turns to seed into ChatModule instance memory as "recent
# history". The unified timeline caps to MERGED_HISTORY_MAX (~30) at load, so a
# little headroom is plenty; the full turn set stays searchable as event memory.
_CHAT_HISTORY_MAX_TURNS = 40


async def _seed_chat_history(db, agent_id: str, user_id: str, narrative, planned: PlannedNarrative) -> None:
    """Seed the Narrative's ChatModule instance memory with the recent turns so
    they load as normal recent history (ChatModule reads this store, NOT the
    event memory). The sort key is each turn's ORIGINAL timestamp — a multi-
    session import writes all narratives at once, so using import-time would
    collapse the cross-narrative timeline; the real turn times keep it correct.
    Best-effort: never break import on a chat-history write."""
    chat_inst = next(
        (i for i in (narrative.active_instances or []) if i.module_class == "ChatModule"),
        None,
    )
    if not chat_inst or not planned.turns:
        return
    try:
        recent = planned.turns[-_CHAT_HISTORY_MAX_TURNS:]
        messages = []
        # For turns missing a timestamp, synthesize one just after the LAST KNOWN
        # timestamp (not the session start) so a gap sits adjacent to its real
        # neighbours instead of jumping to the beginning. Claude .jsonl turns
        # normally all carry timestamps; this is the edge case.
        last_dt = _parse_ts(planned.started_at) or datetime.now(timezone.utc)
        gap = 0
        for t in recent:
            dt = _parse_ts(t.ts)
            if dt is not None:
                last_dt, gap, ts = dt, 0, t.ts
            else:
                gap += 1
                ts = (last_dt + timedelta(milliseconds=gap)).isoformat()
            messages.append({
                "role": t.role,
                "content": t.text,
                "meta_data": {"timestamp": ts, "narrative_id": narrative.id, "source": "imported"},
            })
        memory = {"messages": messages, "updated_at": datetime.now(timezone.utc).isoformat()}
        repo = EventMemoryRepository(agent_id, user_id, db)
        await repo.add_instance_json_format_memory("ChatModule", chat_inst.instance_id, memory)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[migrate.apply] chat-history seed failed for {narrative.id}: {e}")


async def _summarize_session(planned: PlannedNarrative) -> _NarrativeSummary:
    """One helper_llm call → the Narrative's AI fields. Best-effort: on any
    failure (or empty source) fall back to a deterministic summary from the
    title + raw source, so import never breaks on the LLM."""
    src = planned.summary_source.strip()
    if src:
        try:
            result = await get_helper_sdk().llm_function(
                instructions=_SUMMARIZE_INSTRUCTIONS,
                user_input=src,
                output_type=_NarrativeSummary,
            )
            out: _NarrativeSummary = result.final_output
            if out.description or out.current_summary:
                return out
        except Exception as e:  # noqa: BLE001 — summary must never break import
            logger.warning(f"[migrate.apply] session summary llm failed: {e}")
    # deterministic fallback
    return _NarrativeSummary(
        description=planned.title,
        current_summary=src[:2000] or planned.title,
    )


async def _import_narrative(db, agent_id: str, user_id: str, planned: PlannedNarrative) -> int:
    """Create one Narrative from a planned session (summarized) and retain its
    turns as event memory scoped to that Narrative. Returns turns retained."""
    summary = await _summarize_session(planned)
    svc = NarrativeService(agent_id, db)
    narrative = await svc.create_narrative(
        agent_id=agent_id, user_id=user_id, narrative_type=NarrativeType.CHAT,
        title=planned.title or "Imported session",
        description=summary.description or planned.title,
    )
    # enrich the AI fields create_narrative doesn't set, then persist
    narrative.narrative_info.current_summary = summary.current_summary or planned.title
    narrative.topic_keywords = summary.topic_keywords
    narrative.topic_hint = summary.topic_hint
    started = _parse_ts(planned.started_at) or datetime.now(timezone.utc)
    narrative.dynamic_summary = [
        DynamicSummaryEntry(event_id="", summary=m, timestamp=started, references=[])
        for m in summary.dynamic_summary if m.strip()
    ]
    await svc.save_narrative_to_db(narrative)

    # retain the real turns as event memory scoped to this Narrative
    # Also write the recent turns into this Narrative's ChatModule instance
    # memory, so they load as normal "recent history" when the user opens the
    # narrative (the event memory below is search-only — never auto-injected).
    await _seed_chat_history(db, agent_id, user_id, narrative, planned)

    engine = MemoryEngine(db, agent_id)
    retained = 0
    for t in planned.turns:
        try:
            # kind="event" (NOT observation): raw conversation turns are the
            # append-only per-interaction index (default_scope=narrative, does
            # NOT consolidate). Observation consolidates at threshold 4 → it would
            # tombstone these dozens of turns into a few summaries ~90s after
            # import, destroying the very history we imported. Distilled facts
            # (step 2) correctly stay observation.
            await engine.retain(MemoryRecord(
                agent_id=agent_id, scope_type=SCOPE_NARRATIVE, scope_id=narrative.id,
                kind="event",
                content_text=f"{t.role}: {t.text}", tags=["imported", "chat"],
                proof_count=1, valid_at=_parse_ts(t.ts) or datetime.now(timezone.utc),
            ))
            retained += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[migrate.apply] turn retain failed: {e}")
    return retained


async def _awareness_instance_id(db, agent_id: str) -> Optional[str]:
    insts = await InstanceRepository(db).get_by_agent(agent_id, module_class="AwarenessModule")
    return insts[0].instance_id if insts else None


async def _copy_local_skill(agent_id: str, user_id: str, name: str, src: str) -> bool:
    # `name` comes from an editable import payload — keep it a SINGLE safe path
    # segment so it can never traverse out of skills/ (the dest is then rmtree'd
    # + copytree'd, so a `../..` name would delete/overwrite arbitrary dirs).
    safe = Path(name).name
    if not safe or safe in (".", "..") or safe != name:
        logger.warning(f"[migrate.apply] refusing unsafe skill name: {name!r}")
        return False
    src_path = Path(src).expanduser()
    if not src_path.is_dir():
        return False
    skills_root = (agent_workspace_path(agent_id, user_id) / "skills").resolve()
    dest = (skills_root / safe).resolve()
    # Defense-in-depth: the resolved dest must stay under skills_root.
    if dest.parent != skills_root:
        logger.warning(f"[migrate.apply] skill dest escaped skills/: {dest}")
        return False
    def _copy() -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src_path, dest)

    # rmtree + copytree are blocking filesystem work — keep them off the loop.
    await asyncio.to_thread(_copy)
    return True


async def apply_plan(
    db,
    user_id: str,
    plan: MigrationPlan,
    agent_id: Optional[str] = None,
) -> ApplyResult:
    """Create/populate an agent from a plan. `db` is an AsyncDatabaseClient."""
    created = False
    if not agent_id:
        agent_id = f"agent_{uuid4().hex[:12]}"
        await AgentRepository(db).add_agent(
            agent_id=agent_id,
            agent_name=plan.agent_name or "Imported Agent",
            created_by=user_id,
            agent_description="Imported via Agent Migration",
            agent_type="chat",
        )
        # provisions AwarenessModule + the other agent-level module instances
        await InstanceFactory(db).create_agent_level_instances(agent_id)
        created = True
    else:
        await InstanceFactory(db).ensure_agent_instances_exist(agent_id)

    result = ApplyResult(
        agent_id=agent_id,
        created=created,
        warnings=list(plan.warnings),
    )

    svc = None  # lazily-created SkillMarketplaceService, shared by steps 0 & 3

    # 0) Default NarraNexus skills — the same is_default set (netmind-vision,
    #    officecli, ...) a normally-created agent gets. Only for a newly-created
    #    agent; degrades to a no-op when the registry is unreachable (desktop
    #    offline). Runs BEFORE the imported skills so a same-name imported skill
    #    still wins (faithful reproduction overwrites the default copy).
    if created:
        try:
            from xyz_agent_context.marketplace.skill_marketplace_service import (
                SkillMarketplaceService,
            )
            svc = SkillMarketplaceService(db)
            summary = await svc.install_defaults(agent_id, user_id)
            result.default_skills_installed = list(summary.get("installed", []))
            if summary.get("failed"):
                logger.warning(f"[migrate.apply] default skills failed={summary['failed']}")
        except Exception as e:  # noqa: BLE001 — defaults must never break import
            logger.warning(f"[migrate.apply] default skills skipped: {e}")

    # 1) Awareness
    if plan.awareness_markdown.strip():
        inst_id = await _awareness_instance_id(db, agent_id)
        if inst_id:
            await InstanceAwarenessRepository(db).upsert(inst_id, plan.awareness_markdown)
            result.awareness_written = True

    # 2) General Memory
    if plan.memory:
        engine = MemoryEngine(db, agent_id)
        for m in plan.memory:
            try:
                await engine.retain(MemoryRecord(
                    agent_id=agent_id, scope_type=SCOPE_AGENT, kind="observation",
                    subtype="world", content_text=m.content, tags=["imported"],
                    proof_count=1,
                    source_ref={"kind": "import", "id": m.source} if m.source else None,
                ))
                result.memory_written += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[migrate.apply] memory retain failed: {e}")

    # 3) Skills — copy the original files verbatim; marketplace only as fallback.
    for skill in plan.skills:
        try:
            if skill.local_path and await _copy_local_skill(agent_id, user_id, skill.name, skill.local_path):
                result.skills_copied.append(skill.name)
                continue
            # fallback: same-name marketplace install
            if svc is None:
                from xyz_agent_context.marketplace.skill_marketplace_service import (
                    SkillMarketplaceService,
                )
                svc = SkillMarketplaceService(db)
            found = await svc.search(q=skill.name, limit=10)
            match = next(
                (it for it in found.get("items", [])
                 if str(it.get("name", "")).strip().lower() == skill.name.strip().lower()),
                None,
            )
            if match and match.get("skill_id"):
                await svc.install(agent_id, user_id, match["skill_id"])
                result.skills_installed.append(skill.name)
            else:
                result.skills_unmatched.append(skill.name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[migrate.apply] skill '{skill.name}' failed: {e}")
            result.skills_unmatched.append(skill.name)

    # 4) url-MCP (stdio deferred — recorded so the caller can surface it)
    if plan.mcp_url_servers:
        mrepo = MCPRepository(db)
        for srv in plan.mcp_url_servers:
            try:
                await mrepo.add_mcp(
                    agent_id=agent_id, user_id=user_id,
                    mcp_id=f"mcp_{uuid4().hex[:8]}", name=srv.name,
                    url=srv.url or "", headers=srv.headers or None,
                    description=f"imported ({srv.transport})", is_enabled=True,
                )
                result.mcp_added.append(srv.name)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[migrate.apply] mcp '{srv.name}' failed: {e}")
    result.mcp_stdio_skipped = [s.name for s in plan.mcp_stdio_servers]

    # 5) Narratives — one per imported session. Each is summarized (helper_llm),
    #    created + enriched + saved directly (no agent loop), and its turns are
    #    retained as event memory scoped to that Narrative. Best-effort per
    #    session so one bad session never aborts the rest.
    if plan.narratives:
        # Load the OWNER's effective LLM config onto this task so the per-session
        # summarizer's helper_llm uses the USER's configured provider — not the
        # platform default, whose stale key 401s (same defect + fix as every
        # detached background helper task; see providers.resolver
        # inject_owner_helper_credentials). Without this the summaries silently
        # degrade to the deterministic fallback.
        try:
            from xyz_agent_context.agent_framework.providers.resolver import (
                resolve_and_set_provider_for_user,
            )
            await resolve_and_set_provider_for_user(user_id, db, agent_id=agent_id)
        except Exception as e:  # noqa: BLE001 — degrade to fallback summaries, never abort
            logger.warning(f"[migrate.apply] provider resolve failed; summaries degrade: {e}")
    for planned in plan.narratives:
        try:
            n = await _import_narrative(db, agent_id, user_id, planned)
            result.narratives_created.append(planned.title or "Imported session")
            result.memory_turns_retained += n
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[migrate.apply] narrative '{planned.title}' failed: {e}")

    logger.info(
        f"[migrate.apply] agent={agent_id} created={created} "
        f"awareness={result.awareness_written} memory={result.memory_written} "
        f"defaults={len(result.default_skills_installed)} "
        f"skills_copied={len(result.skills_copied)} installed={len(result.skills_installed)} "
        f"unmatched={len(result.skills_unmatched)} mcp={len(result.mcp_added)} "
        f"narratives={len(result.narratives_created)} turns={result.memory_turns_retained}"
    )
    return result
