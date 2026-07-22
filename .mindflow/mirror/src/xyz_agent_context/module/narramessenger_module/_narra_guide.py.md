---
code_file: src/xyz_agent_context/module/narramessenger_module/_narra_guide.py
stub: false
last_verified: 2026-07-21
---

## 2026-07-21 — STOP live-fetching runtime.md; serve a curated reference

**Root cause (dev incident):** `narra_guide` used to live-fetch narra's
`runtime.md` verbatim. That document is written for a runtime that installs /
configures / runs narra-cli **itself** (`npm install @narra-im/narra-cli`,
`configure --endpoint`, `.narra/agent-runtime-token`, and narra-cli's own
`chmod 0700` of `~/.narra-cli`). In OUR architecture narra-cli is
platform-provided via the `narra_cli` MCP tool, so those setup instructions are
actively harmful: with a strong model (Opus 4.8), the agent faithfully followed
the guide, tried to install + `configure` narra-cli in its sandbox, and hit
"narra-cli cannot init its config dir — chmod permission denied". (A weaker model
earlier flailed with Bash and gave a confused "cloud can't execute" apology.)
The `narra_cli` docstring said "don't install", but the detailed guide said "here
is how to install" — and the detailed guide won.

**Fix:** `get_guide()` now returns a STATIC, platform-adapted curated reference
(`resources/narra-runtime.md`) — a strong "platform provides narra-cli, use the
`narra_cli` tool, do NOT install/configure/token/shell-run it" banner plus the
command *shapes* only. No HTTP, no cache, no `backend_base_url`. The whole
live-fetch layer (`_http_get` / `fetch_guide` / TTL cache / aiohttp) was deleted.

## Why it exists

Backs the `narra_guide` MCP tool: an on-demand command reference for the
`narra_cli` passthrough (kept out of every-turn system prompt to save tokens,
same shape as lark's `lark_skill`). narra has no skill packs — one small doc.

## Design decisions

- **Static curated, NOT live.** We deliberately give up "auto-track narra's
  prose" because that prose is setup-oriented and harmful (see above). We do NOT
  lose command freshness: the agent gets exact / latest flags from the live CLI
  via `narra_cli("<domain> --help")`, and the passthrough runs new commands at the
  execution layer regardless. Maintenance is bounded to "narra adds a whole new
  top-level DOMAIN" — which already needs an `ALLOWED_DOMAINS` whitelist edit in
  [[_narra_command_security]], so the two move together.
- **Built-in fallback** keeps the "use the tool, don't set up narra-cli"
  invariant even if the resource file is missing (non-editable wheel edge case).

## Upstream / downstream

- **Called by**: `_narramessenger_mcp_tools.narra_guide` (returns
  `get_guide()`; no credential / network).
- **Reads**: `resources/narra-runtime.md` (the curated reference — the source of
  truth to edit when advertising a new domain).

## Gotchas

- The curated doc's banner NAMES `npm install` / `configure` / `--token` in order
  to FORBID them; that is intentional. What must never appear is the actual
  install RECIPE (`@narra-im/narra-cli`, `./node_modules/.bin/...`) — tests guard
  this.
- If narra ships a genuinely new command surface, update
  `resources/narra-runtime.md` (and the whitelist) — do not re-add live-fetch.
