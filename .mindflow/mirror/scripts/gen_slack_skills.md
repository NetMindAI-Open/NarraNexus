---
code_file: scripts/gen_slack_skills.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Build-time generator that turns Slack's vendored OpenAPI v2 (Swagger)
spec into ~250 per-method markdown files under
``src/xyz_agent_context/module/slack_module/skills/``. Those files are
what the ``slack_skill`` MCP tool serves — without this generator,
the agent has no "look up a method's args/scope/example" path.

Pinned source: ``vendor/slack-api-specs/slack_web_openapi_v2.json``
(SHA recorded in the file header) — committing the spec at a known
revision is what lets us regenerate deterministically and review the
diff in PRs.

## Design decisions

- **Per-method file, named after the literal dotted method.**
  ``chat.postMessage.md``, ``conversations.history.md``, ... — matches
  Slack's identifier exactly so the loader does pure ``Path.stem``
  lookup. No translation, no slug-mangling.
- **Drop ``token`` parameters and ``in: header`` params.** The channel
  injects auth itself; surfacing them to the agent invites
  hallucinated ``token`` values in tool calls.
- **Build a tiny example invocation from required args only.** Agents
  copy the example as a starting point; padding with optional args
  invites them to set values they shouldn't.
- **HTML stripped from descriptions.** Slack's spec embeds ``<a>``,
  ``<code>``, etc. Markdown gets confused; LLMs gracefully ignore
  inline HTML but it's noise. Run ``_strip_html`` + whitespace
  collapse for clean output.
- **Skip deprecated methods.** Both the ``deprecated`` flag and any
  tag containing "deprecated" — agents shouldn't discover dead
  methods at all.
- **``_index.json`` emitted alongside the docs.** Category → methods
  list. The skill loader uses it for "Did you mean..." hints; if it's
  missing the loader recomputes from filenames.
- **Markdown table as the args list.** LLMs read tables well; the
  ``| name | type | required | description |`` format is uniform
  across all 250 docs so the agent's reading pattern transfers.

## Upstream / downstream

- **Upstream**: human / CI runs
  ``uv run python scripts/gen_slack_skills.py`` after bumping the
  vendored spec.
- **Downstream**:
  - Output dir consumed by ``_slack_skill_loader.py`` at runtime.
  - ``vendor/slack-api-specs/slack_web_openapi_v2.json`` is the
    pinned input.

## Gotchas

- The pinned SHA in the file docstring is informational; the actual
  pin is the committed file under ``vendor/``. Update both when
  bumping.
- Generated files aren't checked for hand-edits — running the
  generator clobbers everything. If you need a custom override for
  one method, add a separate non-underscored file with a different
  name and let the loader's category-list pick it up, or add a
  post-process step (currently absent).
- ``operationId`` missing → skipped silently with a log line. Spec
  drift can drop methods without warning. Diff the output dir before
  committing a regen.
