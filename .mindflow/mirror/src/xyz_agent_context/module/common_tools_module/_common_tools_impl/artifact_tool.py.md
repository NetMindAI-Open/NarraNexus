---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_tool.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — new `open_url` MCP tool

Second tool registered alongside `register_artifact`: `open_url(url, title?)`
lets the agent open a web page as a URL tab, delegating to
`ArtifactService.open_url`. Same `{error, code}` failure contract. The tool
name is NOT matched by the frontend live-discovery constant (that is
`register_artifact` only) — URL tabs surface via the normal artifact refresh,
so no ChatPanel coupling to keep in sync here.
## 2026-05-20 — quota wording purged (v1.7.0 removal cleanup)

The per-user artifact quota was removed in v1.7.0 (see [[artifact_runner.py]]),
but stale "quota" wording survived in this tool's LLM-facing `description=` and
in the body docstring/comment. Removed: the description's error-cause list no
longer says "quota" (only "path outside workspace, file missing, too large"),
and the `ArtifactQuotaExceeded` exception / `code === 507` modal references
below are historical only — neither exists anymore. Keeping the stale wording
risked the agent self-limiting or telling users about a quota that's gone.

## 2026-05-15 — description teaches the refresh-signal pattern

The tool description now spells out the **re-register-as-refresh-signal**
contract: after the first register, the agent can keep editing the
workspace file(s) freely, but the frontend doesn't auto-reload. To make
the user see the update, call `register_artifact` again with
`target_artifact_id=<existing id>` — that second call is what the
frontend listens for to refetch the entry HTML and any sibling assets.
The description also points the agent at the per-turn "Your registered
artifacts" system-prompt block so it knows which ids are currently live.

## 2026-05-14-r3 — hard rule dropped; sibling-assets reframed as a capability

The "must sit in a subdirectory, NOT directly in the workspace root"
non-negotiable rule is gone. With `delete_source` removed (deletion is now
registry-only) and the serving route soft-degrading at the workspace root
(only the entry serves), the rule had nothing left to enforce.

The description now phrases the subdirectory requirement as a **capability
hint**: "for a multi-file artifact, put the files in a dedicated
subdirectory so the entry's relative references resolve; single-file
artifacts can sit anywhere including the workspace root". The agent gets a
clean mental model — there are no register-time gotchas, the worst thing
that can go wrong is "my entry HTML references ./style.css and gets 404
because I put it at the workspace root", which is an immediate, fixable
feedback loop.

The catch-all error contract still mentions the cause categories ("path
outside workspace, file missing, too large, quota") so the agent reads the
error and retries.

## 2026-05-14-r2 — tool description spells out *why* workspace root is rejected

The earlier description told the agent "must sit in a subdirectory of your
workspace, not directly in the workspace root" but only implied the reason
("entry file's folder becomes the artifact"). An agent could read that as a
stylistic preference. The description now explicitly states the two
consequences of an entry at the workspace root:
- the whole workspace is served wholesale → every other file (Bootstrap.md,
  other artifacts, drafts) is exposed via the public token URL;
- a later `delete_source=true` would rmtree the entire workspace.

Same explanation is now in the module instruction (see
[[common_tools_module.py]]). The runtime error message already mentioned
"that whole folder becomes the artifact" — kept as-is for terseness.

## 2026-05-14 — collapsed to one `register_artifact` tool (pointer model)

The two old tools — `create_artifact` (inline content) and
`upload_artifact_file` (copy a workspace file) — are replaced by **one**
`register_artifact(entry_path, kind, title, ...)`. The new tool registers a
*pointer* to a file the agent already wrote in its workspace; it never copies,
moves, or writes content.

The description was rewritten around the core mental model the agent must hold:
**files you write are invisible until you register them**; the tool only
registers a pointer; put each artifact in its own dedicated subdirectory so an
entry HTML can reference sibling assets. Catch-all `except Exception` → `{error,
code: 500}` is preserved.

## 2026-07-21 — delegates to ArtifactService

The registration logic moved out of this module's `_common_tools_impl` into
the dedicated `xyz_agent_context/artifact/` package. The tool now delegates to
`ArtifactService.register` and imports the `ArtifactError` hierarchy from
`xyz_agent_context.artifact` — same validation, same error contract; only the
seam changed. Older entries below reference `artifact_runner`, its previous
home.

## Why it exists

Bridges the artifact subsystem's validation + DB logic into an LLM-callable
MCP tool. Each call: resolve the per-loop DB client → construct
`ArtifactService` → delegate to `service.register` → catch structured
exceptions → return an LLM-readable dict.

## Why it lives in common_tools_module, not a dedicated ArtifactModule

Artifact registration is a **capability** tool the LLM may reach for in any
session — not a scenario-specific Module. A dedicated Module would cost an extra
MCP port, process, and prompt-engineering surface; common_tools already has the
full MCP server infrastructure (factory, timeout decorator, backend dispatch).

## Upstream / Downstream

- **Called by**: `_common_tools_mcp_tools.py` → `create_common_tools_mcp_server`
  calls `artifact_tool.register(mcp)`.
- **Depends on**: `xyz_agent_context.artifact.ArtifactService` (validation +
  DB orchestration), `get_db_client` (per-loop DB singleton).
- Does **not** use `with_mcp_timeout` — registration is local I/O (path stat +
  one DB write), no external network call to guard.

## The registered tool — `register_artifact`

- Purpose: surface a workspace file (or multi-file folder via an entry HTML) as
  a visual tab.
- Params: `entry_path` (absolute or workspace-relative, anywhere inside the
  workspace — a dedicated subdirectory enables sibling assets, the workspace
  root gives single-file mode), `kind`, `title`, `target_artifact_id`
  (optional, update-in-place).
- Returns `CreateArtifactToolResult.model_dump(mode="json")` → `{artifact_id,
  url, created_at}`. The description tells the LLM not to repeat the URL in its
  reply (the tab is already visible).

## Error handling

Two layers:
1. `ArtifactError` (+ subclasses `ArtifactTooLarge`, `ArtifactNotFound`,
   `ArtifactKindMismatch`, `ArtifactPathEscape`) →
   `{"error": str(e), "code": e.code}`. Expected, structured, actionable.
2. Catch-all `except Exception` → `{"error": "... likely transient — you can
   call the tool again", "code": 500}`. FastMCP swallows unhandled exceptions
   into opaque MCP errors that can stall the agent loop; the catch-all
   guarantees every failure is a structured, retryable dict.

The `{error, code}` shape is a stable contract — the agent reads it to
self-correct and retry. Don't change the shape.

## Gotchas

- ⚠️ **Frontend coupling**: the tool name `register_artifact` is matched by
  `ARTIFACT_TOOL_BASE_NAMES` in `ChatPanel.tsx` for live artifact discovery.
  Renaming the tool requires updating that constant in the same change.
- `get_db_client()` returns a process-level singleton — `await` it per call,
  don't cache.
- The handler `register_artifact` is a closure inside `register(mcp)` captured
  by the FastMCP decorator — don't lift it to module scope (breaks the
  `register(mcp)` encapsulation pattern shared with the web_search tools).
- `kind` is typed `str` in the MCP schema but the service expects the
  `ArtifactKind` Literal — `# type: ignore[arg-type]`; runtime validation is in
  the registration impl.
