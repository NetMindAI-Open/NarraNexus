---
code_file: src/xyz_agent_context/module/common_tools_module/common_tools_module.py
last_verified: 2026-04-29
stub: false
---

# common_tools_module.py

## Why it exists

Always-on capability module that hosts generic, domain-agnostic tools
every agent benefits from. Today it owns:

1. `web_search` MCP tool (DuckDuckGo or Brave depending on env)
2. The system-prompt instruction block that tells the agent how to use
   the **built-in** `Read` tool to view user-uploaded attachments

Note: there is no MCP tool for attachments — Anthropic's SDK ships the
multimodal `Read` primitive, and our marker text + dynamic instruction
hand it absolute paths. Adding a `load_image` would only confuse the
agent (two ways to do the same thing).

## Upstream / Downstream

Upstream:
- `xyz_agent_context.module.__init__.MODULE_MAP` registers the module
- `xyz_agent_context.module.module_runner` instantiates the MCP server
  on port 7807 (single shared process for all agents)
- `xyz_agent_context.context_runtime.context_runtime` calls
  `get_instructions(ctx_data)` per turn when assembling the system prompt

Downstream:
- `_common_tools_mcp_tools.create_common_tools_mcp_server` creates the
  FastMCP server (`web_search` only)
- `xyz_agent_context.utils.attachment_storage
  .format_attachments_for_system_prompt` renders the current-turn
  attachment block

## Design decisions

**`get_instructions` is dynamic, not a static string.** The base
description is constant, but the per-turn appendix depends on whether
the user uploaded files in this run. We read
`ctx_data.extra_data["attachments"]` (populated by the trigger layer)
and append a `## Files attached to the current message` block listing
absolute paths. The marker in chat history says the same thing again
at the user-message level — double reinforcement so the model can't
miss it.

**Capability-only, no instance state.** The module never queries the
DB, never looks at the user's history. All behavior depends on the
current turn's `ctx_data`. This keeps it cheap to call on every turn
and side-effect-free.

## Gotchas

- The static instruction text talks about the `Read` tool by name. If
  Anthropic ever renames it (unlikely), the prompt will reference a
  ghost. We've accepted this coupling — it's a single string, easy to
  update.
- `self.user_id` can be `None` for some triggers (background jobs); we
  pass `self.user_id or ""` to the storage helper, which then resolves
  to "no workspace" — appendix is empty in that case (correct: no
  user, no attachments).

## New-joiner traps

- Adding a new generic tool here is fine; adding a tool that scopes
  per-agent is NOT — the MCP server is shared, not per-agent.
- Do not add an attachment-specific tool. The whole rewrite that
  removed AttachmentModule was specifically to avoid that — `Read`
  already handles it.
