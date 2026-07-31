---
code_file: src/xyz_agent_context/migration/extractors.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

Per-framework extraction of a source config into the standardized JSON
dimensions (agent / skills / memory / mcp / **sessions**). Best-effort: a
missing/malformed file degrades to empty, never raises (a scan must not crash).
`extract()` returns a 6-tuple `(agent, skills, memory, mcp, custom, sessions)`.

## Real source layouts (verified vs Hermes agent_import.py / openclaw_to_hermes.py, MIT)

- **Claude Code**: MCP is in `~/.claude.json` mcpServers (+ `settings.json`) — NOT
  `~/.claude/.mcp.json`. Instructions live per-project in `<cwd>/CLAUDE.md`;
  global skills in `~/.claude/skills/`; per-project config (incl. mcpServers) in
  `~/.claude.json` `projects[<cwd>]`. Two shapes handled: `~/.claude` (global —
  skills + global mcp) vs a project cwd (CLAUDE.md → system_prompt + project +
  global mcp + project/global skills + sessions).
- **Codex**: `AGENTS.md` → system_prompt; `config.toml` `mcp_servers` (tomllib);
  `memories/*.md` → memory; `skills/`.
- **OpenClaw**: persona/memory under `workspace/{SOUL,MEMORY,USER}.md` +
  `workspace/memory/` (NOT the `~/.openclaw` root); `skills/`; `openclaw.json` mcp.
- **Hermes**: same SOUL/MEMORY/USER at the root.

## Design decisions

- **Awareness = combined CLAUDE.md** (`_combine_claude_md`, 2026-07-30): Claude's
  effective instructions layer global `~/.claude/CLAUDE.md` + project
  `<cwd>/CLAUDE.md` + `<cwd>/CLAUDE.local.md`. We concat all three (section-
  labelled) and cap at `AWARENESS_IMPORT_CHAR_LIMIT` — importing only one loses
  instructions. Awareness is injected wholesale every turn, hence the cap.
- **Skill dedup, project wins** (`_claude_skills`): project `<cwd>/.claude/skills`
  + global `~/.claude/skills`, deduped by name with the PROJECT skill winning a
  same-name clash. Each tagged `scope` (project|global) for the preview.
- **Sessions → per-session Narratives** (`_claude_sessions` / `_parse_claude_session_file`,
  2026-07-30): Hermes ignores sessions; we parse EACH `.jsonl` (one = one session
  = one Narrative downstream) into a `MigrationSession`. Kept: `ai-title` (→ name),
  `isCompactSummary` rollups (the source's own history summary), recent real
  user/assistant text. Dropped: `tool_result` / `tool_use` / `thinking` blocks,
  `isSidechain` / `isMeta` / `isVisibleInTranscriptOnly` lines. Streamed
  (`_iter_jsonl`) with bounded buffers — a single session `.jsonl` can be 100MB+.
- ⚠️ **`_encode_cwd` replaces EVERY non-alphanumeric char with `-`** (`/`, `_`,
  `.` …), not just `/`. A `/`-only replace silently finds zero sessions on real
  data (`xyz_proto_test` → `xyz-proto-test`). The detector reuses this.
- **Secret tagging** (`_flag_secret_fields`): MCP creds hide in `args`
  (`--api-key=…`) and `url` query too, not only env/headers. We flag the dotted
  field paths (`args[i]`, `env.K`, `url`, …) in `secret_fields` so the plaintext
  preview highlights them. VALUES are carried (Owner decision); non-MCP `.env`
  secrets contribute KEY NAMES only.
- Codex/OpenClaw/Hermes: CLAUDE.md/SOUL.md/AGENTS.md → Awareness;
  MEMORY.md/`memories/`/USER.md → Memory. **Their session/compact structure is
  UNVERIFIED** — no session parsing for them yet (see the 2026-07-30 design doc §7).
