---
code_file: src/xyz_agent_context/schema/migration_schema.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

The framework-agnostic contract between the migration Scanner (produces it) and
the consumers — Migration Skill / Import Button (map+write from it). One shape
covers Claude Code / Hermes / OpenClaw / Codex / Custom.

## Design decisions

- `MigrationMcpServer.transport` discriminates `stdio` (command/args/env) vs
  `url` (url/headers). Both are captured so no source info is lost; only `url`
  writes in v1.0 (NarraNexus MCP data model is URL/headers only).
- **Credential policy**: MCP `env`/`headers` VALUES are carried (Owner decision
  2026-07-21 — MCP is useless without its auth; UI shows them plaintext + warns).
  Non-MCP secrets contribute KEY NAMES only via `custom.credential_keys`.
- **Sessions → Narratives** (2026-07-30): `sessions: List[MigrationSession]` —
  one source conversation session becomes one NarraNexus Narrative. Each carries
  `title` (Claude's ai-title), `compact_text` (the source's own history rollup),
  and `turns` (real user/assistant messages, tool/thinking/sidechain filtered).
  The consumer summarizes (compact + recent turns) into the Narrative's AI fields
  and retains `turns` as observation memory scoped to that Narrative. Fully
  replaced the v1 single `session_summary_seed` blob (removed — no back-compat).
- `MigrationSkill.scope` (project|global) — on a same-name clash the project skill
  wins (applier copies project last / dedups project-first).
- `AWARENESS_IMPORT_CHAR_LIMIT` caps the combined global+project+local CLAUDE.md
  that lands in Awareness (injected wholesale every turn, so bounded).
- `FrameworkDetection` is the lighter `detect`-only result (framework + path +
  confidence + matched signals).
