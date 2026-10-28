---
code_file: src/narranexus/platform/schema/migration_schema.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — `OPENCLAW_ALIASES` 移至 `narranexus.contracts.openclaw`

本文件不再定义它；detector/extractors 改从 contracts 取。

## 2026-09-07（批 1 三轮复审移植）— `OPENCLAW_ALIASES`

OpenClaw 生态的全部曾用名（openclaw / clawdbot / clawdis / moltbot）唯一定义处，供 builtin.skills 的 skill_module
（`metadata.<name>`）与 `platform/migration`（home 目录、配置文件名）共用；放在 schema 是因为插件与平台互不可
反向 import，schema 是它们共同的下游。顺序即查找优先级（现名在前）。

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
