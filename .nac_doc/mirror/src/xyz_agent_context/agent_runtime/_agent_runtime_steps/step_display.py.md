---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_display.py
last_verified: 2026-04-10
stub: false
---
# step_display.py — Pipeline Display Formatting Utilities

## Why It Exists

Each pipeline step emits `ProgressMessage` objects that appear in the frontend's turn-progress panel. This module centralizes all display formatting logic so that step files stay focused on business logic and don't embed string manipulation. It converts raw domain objects (Narrative lists, tool call dicts, Module instance records) into human-readable display structures.

## Upstream / Downstream

**Imported by:** Every `step_*.py` file that emits `ProgressMessage` with structured `details` or `substeps`

**Does not call** any external services — pure data transformation functions only

**Consumed by:** Frontend WebSocket handler, which reads `ProgressMessage.details.display` and renders it in the progress panel

## Key Design Decisions

### MODULE_DISPLAY_CONFIG and TOOL_DISPLAY_CONFIG Dicts
These two dicts map internal class names / tool name patterns to display metadata: human-readable labels, icons, and color hints. Centralizing them here means that adding a new Module or tool only requires one edit in one file — not hunting through every step file.

Example structure:
```python
MODULE_DISPLAY_CONFIG = {
    "ChatModule": {"label": "Chat", "icon": "💬"},
    "JobModule": {"label": "Jobs", "icon": "⏰"},
    ...
}
```

### format_tool_call_for_display() Prefix Stripping
MCP tool names arrive as `mcp__<server_name>__<tool_name>`. This function strips the `mcp__xxx__` prefix before displaying to users. The double-underscore separator is an MCP protocol convention; users should see `create_job`, not `mcp__job_module__create_job`.

### format_relative_time_cn() Human-Readable Time
Converts UTC timestamps to relative time strings (e.g., "3 minutes ago", "just now"). Used for Narrative and Module instance time displays. Despite the `_cn` suffix (originally for Chinese locale), the output language should follow the codebase's English-only rule in new additions.

### format_narrative_for_display()
Takes a list of `Narrative` objects and a `scores` dict (similarity scores from vector search) and returns a structured dict with `summary` string and `items` list. The `score` field is included only when the scores dict has a matching entry, so forced-Narrative paths (no scores) render cleanly without "score=None" noise.

## Gotchas / Edge Cases

- **TOOL_DISPLAY_CONFIG key matching**: Keys are matched by prefix, not exact match, because tool names include dynamic suffixes (e.g., `search_jobs_123`). The matching logic uses `str.startswith()` — be careful with tools whose names share prefixes.
- **Scores dict may be empty**: For forced Narratives (Job triggers), `scores` is `{}`. All score-dependent formatting must use `.get()` with a default.
- **Icon characters in progress messages**: Some frontends (non-Unicode terminals) may render emoji incorrectly. Icons are cosmetic — never use them as keys or identifiers.
- **format_relative_time_cn naming**: The `_cn` suffix is a historical artifact from when this was Chinese-only output. The function now accepts a `lang` parameter, but callers still use the old name. Don't rename without updating all call sites.

## Common New-Developer Mistakes

- Adding business logic to this file: it's pure formatting. If you need to filter Narratives or compute scores, do it in the step file before calling these formatters.
- Hardcoding display strings in step files: always add to `MODULE_DISPLAY_CONFIG` / `TOOL_DISPLAY_CONFIG` here instead.
- Assuming `items` list from `format_narrative_for_display()` is sorted: it preserves the input list order (which is already relevance-sorted by Step 1's vector search).
