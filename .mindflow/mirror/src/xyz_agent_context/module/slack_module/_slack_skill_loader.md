---
code_file: src/xyz_agent_context/module/slack_module/_slack_skill_loader.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Serves the ``slack_skill(method)`` MCP tool. Reads per-method markdown
docs from ``skills/`` (~250 files generated from the Slack OpenAPI
spec at build time by ``scripts/gen_slack_skills.py``) and returns the
content on demand.

The whole point: instead of stuffing 250 method specs into every
agent's system prompt, the agent looks up only what it needs at the
moment — same on-demand discovery pattern Lark uses for its FAR
smaller method surface.

## Design decisions

- **Lazy doc-content reads.** ``_build_index`` walks the directory
  once and builds a ``filename → Path`` map. Doc content is read by
  ``get(method)`` only when an agent actually requests it. ~700 KB
  of static markdown never enters memory unless used.
- **Module-level singleton.** First ``get_skill_loader()`` call
  builds the index; subsequent calls return the cached instance.
  The MCP server is long-lived, so we pay the directory-scan cost
  exactly once per process.
- **``_index.json`` provides categories, computed fallback otherwise.**
  The generator emits a category index for nicer hint output, but
  if the JSON is missing or malformed we fall back to extracting
  the prefix before the first dot. The loader never fails to start.
- **Unknown-method response is a helpful hint, not an error.** When
  the agent asks for ``"chat.postmessage"`` (wrong case) we want it
  to self-correct on the next call. Listing same-category methods
  ("Did you mean ``chat.postMessage``?") makes that one-shot.
- **File stems preserve literal dots.** ``chat.postMessage.md`` —
  not ``chat__postMessage.md`` or ``chat-postMessage.md``. Path lookup
  matches Slack's method names exactly so the agent never has to
  translate naming conventions.
- **Underscored files are skipped** (``_index.json``, ``_README.md``).
  Reserved namespace for generator output and human-authored extras.

## Upstream / downstream

- **Upstream**: ``slack_skill`` MCP tool (``_slack_mcp_tools.py``).
  Also called by ``slack_cli`` to warn on unknown method names.
- **Downstream**: filesystem (``skills/*.md``) only — no DB, no
  network. The directory is populated by
  ``scripts/gen_slack_skills.py``.

## Gotchas

- Index is built at module import / first-access. New skill files
  added at runtime are NOT picked up — restart the MCP server after
  regenerating.
- ``OSError`` on the actual file read is caught and returned as a
  string to the agent. That's intentional (same self-correcting
  ergonomic) but it can mask real disk problems — if you see "Error
  reading skill file" in production logs, treat it as infra alert.
- The ``del agent_id`` in the MCP tool means this loader is NOT yet
  per-agent. Adding agent-specific skill overrides will need a
  different cache key strategy.
