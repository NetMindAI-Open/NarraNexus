---
code_file: src/xyz_agent_context/migration/mapper.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

The **convert** step: `build_plan(StandardizedAgentImport) -> MigrationPlan`
turns the scanner's framework-agnostic JSON into the concrete write operations a
consumer executes. Pure + side-effect-free so it is unit-testable and shared by
both consumers (Import Button backend, Migration Skill) — they build the same
plan, then execute it their own way.

## The plan

- `awareness_markdown` ← `agent.system_prompt` (Owner: instructions → Awareness).
- `memory` ← `memory[]` (written via the general-memory retain path).
- `skills` ← `skills[]` (`PlannedSkill{name, local_path, scope}`; local copy
  wins, marketplace is the name-only fallback).
- **`narratives`** ← `sessions[]` (2026-07-30): one `PlannedNarrative` per source
  session. `title` (Claude ai-title) becomes the Narrative name directly (no LLM);
  `summary_source` = the source's own compact rollup + rendered recent turns,
  capped — the text the consumer feeds ONE helper_llm call to fill the Narrative's
  AI fields; `turns` are retained as `event` memory scoped to that Narrative
  (append-only; observation would be consolidated away — see [[applier.py]]).
- `mcp_url_servers` vs `mcp_stdio_servers`: url is importable now (mcp API);
  stdio is captured but deferred to local-mode wiring (v1.1) — surfaced so the
  user sees it.
- `warnings`: plaintext-secret fields, stdio-not-imported, unmapped files,
  credential keys not imported, a note of how many sessions become Narratives,
  custom-framework caution — for the preview UI.
