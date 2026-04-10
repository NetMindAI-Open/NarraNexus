# config.py

Static algorithm tuning constants for the agent context module — values that are not environment-specific but that developers may want to tweak during experimentation.

## Why it exists

While `settings.py` covers environment-specific configuration (API keys, database URL, file paths), some constants govern the algorithm's behavior in ways that are unlikely to vary between deployments but may need adjustment when tuning the system. `config.py` is the designated home for those constants so they are visible in one place rather than hardcoded at the call site in the middle of a function.

## Upstream / Downstream

**Consumed by:** `narrative/` (Narrative LLM update scheduling). Any future constants that govern Narrative selection, embedding frequency, or context window limits should live here.

**Depends on:** nothing — pure constants, no imports.

## Design decisions

**Separate from `settings.py`.** Constants in `config.py` are code-level tuning parameters, not deployment configuration. They are versioned with the codebase and changing them requires a code change and redeploy, not just a `.env` edit. Mixing them into `Settings` would imply they can be set per-environment via environment variables, which is misleading.

**`NARRATIVE_LLM_UPDATE_INTERVAL = 1`.** This constant controls how many Events must accumulate before the LLM is asked to refresh a Narrative's metadata (name, summary, keywords). At `1`, every event triggers an update. Setting it to `3–5` reduces LLM call costs at the cost of slightly stale Narrative metadata. The comment in the file explicitly notes this trade-off.

## Gotchas

**The file is nearly empty.** Most constants that began here have since migrated elsewhere or been inlined. Do not mistake its small size for low importance — `NARRATIVE_LLM_UPDATE_INTERVAL` directly controls LLM spending in production.

**New-contributor trap.** Changing `NARRATIVE_LLM_UPDATE_INTERVAL` to a high value (e.g., `100`) to reduce costs in development will cause Narrative metadata to become stale quickly when running any multi-turn tests, making embedding-based retrieval less accurate. Revert to `1` (or `3`) before testing retrieval quality.
