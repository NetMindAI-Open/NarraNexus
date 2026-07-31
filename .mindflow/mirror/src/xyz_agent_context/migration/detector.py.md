---
code_file: src/xyz_agent_context/migration/detector.py
stub: false
last_verified: 2026-07-28
---

## Why it exists

Framework detection for the migration Scanner. Given the user's local
filesystem, decides which of Claude Code / Hermes / OpenClaw / Codex a directory
looks like (or `custom`), with a confidence. Detect-only — reads no secrets,
writes nothing.

## Design decisions

- **Signal table (`_SIGNALS`)**, not hard-coded ifs: each framework declares its
  `home_dirs` (standard `~/.<name>` locations, incl. OpenClaw's legacy
  `.clawdbot`/`.moltbot`), `strong` files (high confidence), `weak` files
  (medium). Adding a framework = one table entry.
- `classify_path` scores an explicit dir against all frameworks and returns the
  best; falls back to `custom` (low) when nothing matches — the Custom-Importer
  heuristic + LLM fallback handles that downstream.
- `detect_all` probes the standard home locations and returns the best hit per
  framework, so a machine with several frameworks yields several detections.
- **Claude Code is expanded PER PROJECT** (`_claude_code_projects`): Claude Code
  is per-project + per-session, so the unit of "one imported agent" is one
  project, not the whole install. `detect_all` reads `~/.claude.json`'s
  `projects` map and emits one detection per project cwd (path = cwd; signals
  carry `has:CLAUDE.md` + `sessions:N`), ranked by confidence then session
  count. The global `~/.claude` entry is kept but **demoted to `low`** with a
  `global-shared-config` signal — a fallback for grabbing shared skills+MCP
  only, since it has no project persona/sessions. **Denoise filter**: the
  `projects` map holds every dir ever opened in Claude Code, so a project is
  enumerated only if it has real content — a `CLAUDE.md` OR ≥1 session; a bare
  "opened once" cwd with neither is dropped. This is why the picker shows
  several "Claude Code · <project>" rows, not one, and not the noise.

## Gotcha

- `AGENTS.md` is a Codex strong signal AND a generic custom hint, so a bare
  `AGENTS.md` dir classifies as Codex (medium), not custom — intentional.
- Project detections come from `~/.claude.json`'s `projects` keys (absolute
  cwd strings); session counts come from `~/.claude/projects/<encoded-cwd>/*.jsonl`
  where the encoding replaces **every** non-alphanumeric char with `-` (reuses
  `extractors._encode_cwd` — a `/`-only replace silently counts zero sessions).
