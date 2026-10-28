---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/_lark_skill_loader.py
stub: false
last_verified: 2026-04-22
---

## Why it exists

Bridges the on-disk Lark skill packs (installed via
`npx skills add larksuite/cli -g` into `~/.agents/skills/lark-*/`) and
the `lark_skill` MCP tool. This is the **only** path from the Agent to
Lark skill documentation — the Agent's own `Read`/`Glob`/`Grep` tools
live inside a per-agent workspace sandbox and cannot reach these files.

## Design decisions

- **Arbitrary-path read, not just SKILL.md.** `load_skill_file(name, path)`
  accepts any relative path inside the skill directory. Skills are not
  a two-level hierarchy; `lark-whiteboard` has `routes/` and `scenes/`
  subdirs, `lark-slides` has `references/*.xml` data files, and
  `references/*.md` files themselves cross-link to siblings. A single
  `path="SKILL.md"` hook would leave the Agent unable to follow any
  cross-reference.
- **Path containment.** `_resolve_safe` runs
  `(skill_dir / path).resolve().relative_to(skill_dir.resolve())` to
  reject `../etc/passwd` and `/absolute/paths`. The skill dir is the
  hard boundary.
- **Inline link rewriting.** Every markdown link of the form
  `[text](relative/path.md)`, `[text](routes/*.md)`, `[text](scenes/*.md)`,
  and cross-skill `[text](../lark-other/...)` is rewritten in place to
  `[text — call lark_skill(agent_id, "<skill>", path="<file>")](target)`.
  The original `](target)` stays so the markdown still renders for
  humans; the visible hint next to `text` spells out the correct MCP
  call for the Agent. HTTP/HTTPS URLs and mailto links are left alone.
- **Banner prepended to every .md return.** Reminds the Agent, on every
  turn, that `Read`/`Glob`/`Grep` cannot see these files. Defence in
  depth — the Agent also gets the rule in `lark_module.py`'s
  `_build_skill_section` system prompt and the `lark_skill` docstring.
- **Non-markdown files returned verbatim.** XML schemas (slides), JSON
  samples, and similar data files pass through unmodified — the banner
  and link rewriter apply only to `.md`.
- **Two search paths, env var override.** `~/.claude/skills/` and
  `~/.agents/skills/` (first found wins). `LARK_SKILLS_DIR` can override
  for tests and non-standard deployments.

## Upstream / downstream

- **Upstream**: `_lark_mcp_tools.py` `lark_skill` MCP tool is the sole
  public caller of `load_skill_file`. `lark_module.py` calls
  `get_available_skills()` to enumerate names in the system prompt.
- **Downstream**: filesystem (reads `.md` / `.xml` / `.json` etc.).

## Gotchas

- Skills are re-discovered on every tool call; no caching. Reinstalling
  `larksuite/cli` at runtime takes effect without a restart.
- The link-rewrite regex deliberately targets only extensions we know
  are internal skill data (`.md`, `.xml`, `.json`, `.yaml`, `.yml`,
  `.txt`). Arbitrary file extensions in user-authored markdown bodies
  would be left as-is. If lark skill authors start embedding other
  formats, extend `_MD_LINK_RE`.
- Cross-skill links rewrite to `lark_skill(..., path="SKILL.md")` or
  the exact inner path — they do NOT recursively validate that the
  target skill exists. The Agent will hit a "not found" MCP response
  if the author linked to a missing skill; that's informative enough.
