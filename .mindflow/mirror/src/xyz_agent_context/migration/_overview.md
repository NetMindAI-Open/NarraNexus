---
code_file: src/xyz_agent_context/migration/
stub: false
last_verified: 2026-07-21
---

# migration/ — Agent Migration Scanner (detect + extract)

The **first build** of the Agent Migration feature (import an agent from
Claude Code / Hermes / OpenClaw / Codex into NarraNexus). This package is the
**Scanner**: it reads the user's LOCAL filesystem and produces the framework-
agnostic standardized JSON (`schema/migration_schema.py`). It **never writes**
to NarraNexus and **never extracts non-MCP secrets** — the map+write half is
done later by the Migration Skill (MCP tools) / Import Button (API).

Modeled on Hermes `import-agent` / `claw migrate` (Nous Research): same
scan-source-dirs → detect-framework → extract-by-file pattern.

## Pieces
- `detector.py` — signal-driven framework detection (`detect_all`, `classify_path`).
- `extractors.py` — per-framework file→dimension extraction (best-effort, never
  raises). Layouts verified against Hermes `agent_import.py` (MIT): Claude MCP is
  in `~/.claude.json` (not `.mcp.json`); Codex reads `config.toml`; OpenClaw
  persona/memory under `workspace/`. Also flags which MCP fields carry secrets
  (`secret_fields`) — secrets hide in args/url too, not just env/headers.
- `scanner.py` — public `detect()` / `scan()` → StandardizedAgentImport. Adds the
  Claude **session** layer (`~/.claude/projects/<encoded-cwd>/*.jsonl` → one
  `MigrationSession` per session) — Hermes ignores sessions; this is our
  differentiator.
- `mapper.py` — the **convert** step: `build_plan(StandardizedAgentImport) →
  MigrationPlan` (awareness / memory / skills / per-session `narratives` /
  url-vs-stdio MCP split / warnings). Pure; both consumers build the same plan.

## Write-path note
`applier.apply_plan` executes the plan directly (no HTTP, no agent loop):
Awareness → `InstanceAwarenessRepository.upsert`; General Memory + imported
session turns → `MemoryEngine.retain`; skills → verbatim copy or Skill
Marketplace install; url-MCP → `MCPRepository.add_mcp`; each session → a Narrative
via `NarrativeService` (summarized by one helper_llm call). No embeddings —
narrative routing is BM25.

## Local-only
Reached via `backend/routes/migrate.py` (`/api/migrate/*`), which is **disabled
on cloud** (no user filesystem there). In local/desktop mode the backend +
executor run on the user's machine, so stdio-MCP servers are even coherent to
import (v1.1 wiring).

## Phasing (intent carried here, not in the author's local notebook)

- **v1.0 (shipped)** — full **Claude Code** import: combined CLAUDE.md → Awareness;
  project+global skills (project wins); url-MCP; **per-session → Narrative** (one
  helper_llm summary each, turns kept as `event` memory scoped to the Narrative).
  Import Button UI + once-per-user guided flow. Local-only (503 on cloud).
- **v1.1** — **stdio-MCP** wiring (needs a local-mode MCP data-model extension;
  captured + surfaced today, not imported); **Codex / OpenClaw / Hermes session &
  compact support** once their real layouts are verified (only Claude Code is
  verified — until then those get static awareness/memory/skill/url-MCP mapping,
  no per-session Narratives). See §7 of the design.
- **v2.0** — a **Migration Skill**: agent-driven import via MCP tools (the same
  `apply_plan` core), so the agent can import on its own, not just the button.

> Fuller rationale (superseded gaps, alternatives weighed) lived in
> `reference/self_notebook/specs/2026-07-{21,30}-agent-migration-*.md` (author's
> local notebook, gitignored). The binding conclusions are the phasing above.
