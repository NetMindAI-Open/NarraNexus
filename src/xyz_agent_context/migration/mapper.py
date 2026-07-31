"""
@file_name: mapper.py
@author: NetMind.AI
@date: 2026-07-21
@description: Migration mapping — the "convert" step (standardized JSON → a
concrete write plan for a NarraNexus agent).

Pure + side-effect-free: `build_plan(StandardizedAgentImport) -> MigrationPlan`
turns the scanner's framework-agnostic JSON into the exact operations a consumer
executes:
- Awareness  ← agent.system_prompt (the imported persona/instructions)
- General Memory ← memory[]  (applier writes via MemoryEngine.retain)
- Skills     ← skills[]      (local copy, else name-matched marketplace install)
- MCP        ← mcp_servers[] transport=url  (importable now via the mcp API);
              transport=stdio deferred to local-mode wiring (v1.1)
- Narrative  ← sessions[]  (one PlannedNarrative per session; the consumer
              summarizes each via helper_llm + keeps its turns as memory)

Both consumers (Import Button backend, Migration Skill) build the same plan.
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from xyz_agent_context.schema.migration_schema import (
    MigrationMcpServer,
    StandardizedAgentImport,
)


class PlannedMemory(BaseModel):
    content: str
    source: str = ""


class PlannedSkill(BaseModel):
    name: str
    # Source dir to copy verbatim (faithful reproduction). None → try marketplace.
    local_path: Optional[str] = None
    # project | global | "" — a same-name project skill wins (dedup done upstream).
    scope: str = ""


class PlannedTurn(BaseModel):
    role: str
    text: str
    ts: str = ""


class PlannedNarrative(BaseModel):
    """One source session → one Narrative to create.

    `summary_source` is the text the consumer feeds a helper_llm to fill the
    Narrative's AI fields (description / current_summary / topic_hint /
    dynamic_summary); `turns` are retained as event memory scoped to the
    created Narrative. `title` (Claude's ai-title) becomes the Narrative name
    directly (no LLM).
    """

    session_id: str
    title: str
    started_at: str = ""
    summary_source: str = ""
    turns: List[PlannedTurn] = Field(default_factory=list)


class MigrationPlan(BaseModel):
    agent_name: str = ""
    # The imported instructions become the agent's Awareness (Owner decision:
    # CLAUDE.md/SOUL.md/AGENTS.md → Awareness, not Memory).
    awareness_markdown: str = ""
    memory: List[PlannedMemory] = Field(default_factory=list)
    # Skills to reproduce. `local_path` set → copy the files verbatim (faithful
    # migration); else fall back to a same-name Skill Marketplace install.
    skills: List[PlannedSkill] = Field(default_factory=list)
    # One planned Narrative per source session (Owner: session → Narrative).
    narratives: List[PlannedNarrative] = Field(default_factory=list)
    # url-MCP servers importable now (name/url/headers).
    mcp_url_servers: List[MigrationMcpServer] = Field(default_factory=list)
    # stdio-MCP servers captured but NOT wired yet (need local-mode data-model
    # extension, v1.1) — surfaced so the user sees them.
    mcp_stdio_servers: List[MigrationMcpServer] = Field(default_factory=list)
    # Human-facing warnings for the preview (secrets, unsupported, unmapped).
    warnings: List[str] = Field(default_factory=list)


# Cap on the text handed to the per-session summarizer LLM.
_SUMMARY_SOURCE_CHAR_LIMIT = 24_000


def _summary_source(session) -> str:
    """Text fed to the summarizer: the source's own compact rollup first (already
    condensed history), then the recent real turns rendered as a transcript."""
    parts = []
    if session.compact_text.strip():
        parts.append(f"[Earlier history summary]\n{session.compact_text.strip()}")
    if session.turns:
        transcript = "\n".join(f"{t.role.capitalize()}: {t.text}" for t in session.turns)
        parts.append(f"[Recent turns]\n{transcript}")
    return "\n\n".join(parts)[:_SUMMARY_SOURCE_CHAR_LIMIT]


def build_plan(imp: StandardizedAgentImport) -> MigrationPlan:
    plan = MigrationPlan(
        agent_name=imp.agent.name or "Imported Agent",
        awareness_markdown=imp.agent.system_prompt or "",
        memory=[PlannedMemory(content=m.content, source=m.source_file) for m in imp.memory],
        skills=[PlannedSkill(name=s.name, local_path=s.local_path, scope=s.scope) for s in imp.skills],
        narratives=[
            PlannedNarrative(
                session_id=s.session_id,
                title=s.title or "Imported session",
                started_at=s.started_at,
                summary_source=_summary_source(s),
                turns=[PlannedTurn(role=t.role, text=t.text, ts=t.ts) for t in s.turns],
            )
            for s in imp.sessions
        ],
    )

    for srv in imp.mcp_servers:
        if srv.transport == "url":
            plan.mcp_url_servers.append(srv)
        else:
            plan.mcp_stdio_servers.append(srv)
        if srv.secret_fields:
            plan.warnings.append(
                f"MCP '{srv.name}' carries secrets in {', '.join(srv.secret_fields)} "
                f"— shown in plaintext; confirm before importing."
            )

    if plan.mcp_stdio_servers:
        names = ", ".join(s.name for s in plan.mcp_stdio_servers)
        plan.warnings.append(
            f"{len(plan.mcp_stdio_servers)} stdio MCP server(s) ({names}) are captured "
            f"but not imported yet (local-mode wiring is v1.1); set up equivalents manually."
        )

    if plan.narratives:
        plan.warnings.append(
            f"{len(plan.narratives)} session(s) will each be summarized into a "
            f"Narrative (helper LLM) with their turns kept as memory."
        )

    if imp.custom.unmapped_files:
        plan.warnings.append(
            f"{len(imp.custom.unmapped_files)} unmapped file(s): "
            f"{', '.join(imp.custom.unmapped_files[:8])}"
        )
    if imp.custom.credential_keys:
        plan.warnings.append(
            f"{len(imp.custom.credential_keys)} credential key(s) were NOT imported "
            f"(re-enter in Settings): {', '.join(imp.custom.credential_keys[:8])}"
        )
    if imp.source.framework == "custom":
        plan.warnings.append("Custom/unknown framework — review the mapping carefully.")

    return plan
