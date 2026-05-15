---
code_file: src/xyz_agent_context/module/telegram_module/_telegram_skill_loader.py
stub: false
last_verified: 2026-05-09
---

## Why it exists

Indexes the hand-curated ``skills/*.md`` directory at startup and
serves per-method markdown docs to the ``tg_skill`` MCP tool on
demand. Lazy-reads each file so we don't pay the full doc-set memory
cost on import.

Telegram has NO official OpenAPI / machine-readable schema. Every
method doc here is hand-written, scoped to the high-traffic ~25 of
~100 Bot API methods. ``tg_cli`` still works for non-curated methods
(text-mode plumbing is identical); ``tg_skill`` is the discovery
ergonomics layer.

## Design decisions

- **Hand-curated 27 method docs (Phase 4 scope).** Covers the
  high-traffic chat / message / admin / webhook / bot_info methods
  that the prompt advertises. Multimodal methods (``sendPhoto``,
  ``sendVoice``, ``sendDocument``, ``sendVideo``, ``sendAudio``,
  ``sendAnimation``, ``sendSticker``) are deliberately skipped —
  Phase 4 is text-only on ingress and outbound multimedia is
  reachable via ``tg_cli`` against the live API. Adding these is a
  Phase 5+ scope decision.
- **Filenames match method names exactly.** ``sendMessage.md``,
  ``getUpdates.md``, etc. No translation table.
- **No dots in filenames.** Telegram methods are camelCase single
  tokens — different from Slack's ``chat.postMessage.md``. The
  loader inherits this constraint from the source.
- **``_index.json`` is optional.** Provides ``categories`` for the
  "Unknown method?" fallback hint. If missing the loader still works
  with method-name resolution.
- **Module-level singleton via ``get_skill_loader()``.** Process-
  shared cache; index built once. Adding a method requires adding
  the .md file + restarting the MCP server (no hot reload).
- **Unknown-method fallback is a friendly hint, not an error.**
  Returns the categories list and explicit "Telegram has ~100 methods
  total; we curate ~25" note so the agent learns to call ``tg_cli``
  directly with the live Bot API docs URL when the curated set
  doesn't cover its target.
- **Lazy file read in ``get()``.** Index is small (paths only); doc
  bodies are read at request time. A typical session reads 2-3 docs;
  eager-load would multiply memory by ~25× for no gain.
- **``_*.md`` files (e.g. ``_index.json``) are skipped during
  indexing.** Reserves the underscore prefix for loader-internal docs.

## Upstream / downstream

- **Called by**: ``_telegram_mcp_tools.tg_skill`` (per-call) and
  ``_telegram_mcp_tools.tg_cli`` (warning-line lookup
  ``loader.list_methods()``).
- **Reads**: ``./skills/*.md`` and ``./skills/_index.json``.

## Gotchas

- The 27-method curation is a snapshot; Telegram adds methods
  occasionally. ``tg_cli`` does NOT verify the method exists upstream
  — a typo in a curated method name still POSTs to the API and gets
  Telegram's own 404-equivalent.
- Skipping multimedia methods is intentional. If you add
  ``sendPhoto.md`` you also need to widen ingress in
  ``telegram_trigger.parse_event`` (currently drops non-text).
- Hot reloading isn't supported. Edit a skill.md → restart MCP.
