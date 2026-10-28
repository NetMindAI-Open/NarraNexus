"""
@file_name: migration_schema.py
@author: NetMind.AI
@date: 2026-07-21
@description: Standardized JSON contract for Agent Migration (import → NarraNexus).

The framework-agnostic schema produced by the Scanner (detect+extract) and
consumed by the Migration Skill / Import Button (map+write). One shape for
Claude Code / Hermes / OpenClaw / Codex / Custom sources.

Design: reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md
Scanner NEVER writes; NEVER extracts non-MCP secrets (only their key names).
MCP credentials ARE carried (Owner decision 2026-07-21) so imported MCP
servers work; the UI shows them plaintext with a warning.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Framework = Literal["claude_code", "hermes", "openclaw", "codex", "custom"]
Confidence = Literal["high", "medium", "low"]

# Awareness is injected wholesale every turn, so the combined imported
# instructions (global + project + local CLAUDE.md) are truncated to this cap.
AWARENESS_IMPORT_CHAR_LIMIT = 24_000


class MigrationSource(BaseModel):
    """Which framework we detected and how sure we are."""

    framework: Framework
    detected_path: str
    detection_confidence: Confidence = "high"


class MigrationAgent(BaseModel):
    name: str = ""
    # The source system prompt / persona (CLAUDE.md, SOUL.md, AGENTS.md, ...).
    # Mapped to NarraNexus Awareness downstream.
    system_prompt: str = ""
    description: str = ""


class MigrationSkill(BaseModel):
    name: str
    # Where it came from in the source (clawhub, local dir, github, ...).
    source: str = ""
    # A hint for how it was installed at the source (informational).
    install_hint: str = ""
    # Absolute path to the skill's source directory on the local machine, when
    # the source shipped the skill's files (Claude Code / OpenClaw skills dirs).
    # Migration copies these VERBATIM (faithful reproduction) — preferred over a
    # same-name marketplace skill, which may be a different implementation.
    local_path: Optional[str] = None
    # Where the skill came from within the framework: a per-project skill vs a
    # user-global one. On a same-name clash the project skill wins (see applier).
    scope: Literal["project", "global", ""] = ""


class MigrationMemory(BaseModel):
    # e.g. "fact" | "note" | "profile"
    type: str = "fact"
    content: str
    # Which source file this memory came from (for the "source annotation").
    source_file: str = ""


class MigrationMcpServer(BaseModel):
    name: str
    # stdio = local process (command/args/env); url = remote endpoint (url/headers).
    transport: Literal["stdio", "url"] = "stdio"
    # stdio transport
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    # MCP credentials ARE carried (Owner decision) — shown plaintext + warned in UI.
    env: Dict[str, str] = Field(default_factory=dict)
    # url transport
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    # Which fields carry secret VALUES (e.g. ["args", "url", "env.TOKEN",
    # "headers.Authorization"]). MCP secrets live not only in env/headers but
    # embedded in args (--api-key=...) and url query — the preview UI must
    # highlight these when it shows them plaintext (Owner decision).
    secret_fields: List[str] = Field(default_factory=list)


class MigrationTurn(BaseModel):
    """One real conversation turn extracted from a source session."""

    role: Literal["user", "assistant"]
    text: str
    # ISO timestamp from the source transcript line, when present.
    ts: str = ""


class MigrationSession(BaseModel):
    """One source conversation session → one NarraNexus Narrative.

    For Claude Code, one `.jsonl` under ~/.claude/projects/<encoded-cwd>/.
    `compact_text` is the source's own history rollup (Claude's
    isCompactSummary lines) — high-value pre-summarized context; `turns` are
    the real user/assistant messages (tool calls / thinking / sidechains
    filtered out). The consumer summarizes (compact + recent turns) into the
    Narrative's AI fields and retains `turns` as observation memory scoped to
    that Narrative.
    """

    session_id: str
    # Human-readable title (Claude Code: the rolling ai-title). Narrative name.
    title: str = ""
    # The source's own compacted-history summary text, if any.
    compact_text: str = ""
    turns: List["MigrationTurn"] = Field(default_factory=list)
    # ISO timestamp of the session's start (earliest line / file mtime).
    started_at: str = ""


class MigrationCustom(BaseModel):
    # Files the scanner recognised but did not map to a dimension.
    unmapped_files: List[str] = Field(default_factory=list)
    # Names of non-MCP secrets found (values NEVER extracted) — user re-enters.
    credential_keys: List[str] = Field(default_factory=list)
    # Notes from the Custom-Importer LLM fallback, if used.
    llm_fallback_notes: str = ""


class StandardizedAgentImport(BaseModel):
    """The full contract between Scanner and the Migration consumers."""

    schema_version: str = SCHEMA_VERSION
    source: MigrationSource
    agent: MigrationAgent = Field(default_factory=MigrationAgent)
    skills: List[MigrationSkill] = Field(default_factory=list)
    memory: List[MigrationMemory] = Field(default_factory=list)
    mcp_servers: List[MigrationMcpServer] = Field(default_factory=list)
    # Per-session conversation history → one Narrative each (see MigrationSession).
    sessions: List[MigrationSession] = Field(default_factory=list)
    custom: MigrationCustom = Field(default_factory=MigrationCustom)


class FrameworkDetection(BaseModel):
    """One `detect` result — a candidate source on disk."""

    framework: Framework
    path: str
    confidence: Confidence
    # Human-readable signals that matched (for the preview UI).
    signals: List[str] = Field(default_factory=list)
