---
code_file: src/xyz_agent_context/bootstrap/profiles.py
last_verified: 2026-07-21
stub: false
---

# profiles.py — bootstrap as a pluggable profile (not one hard-coded set)

## Why it exists

The first-run "bootstrap" experience used to be one hard-coded set (greeting +
Bootstrap.md + a `>= 3` deletion rule) scattered across `bootstrap/template.py`,
`context_runtime.py`, and `chat_module.py`, which drifted (the Arena work hit a
dup-greeting bug from exactly that). A `BootstrapProfile` makes the whole
first-run flow a named, pluggable object: `default`, `none`, `arena`, … each
owns its greeting, its Bootstrap.md, its rule-based deletion threshold, and an
optional **welcome artifact** (`welcome_artifact(ctx)` → a pinned text/html card
rendered into the workspace by `apply_bootstrap._create_welcome_artifact`, which
reuses `ArtifactService.register` (xyz_agent_context/artifact, 2026-07-21 —
previously `artifact_runner`) so file_path/size_bytes are correct;
content comes from `bootstrap/welcome_templates.py`).

Agent creation selects a profile by name (a `bootstrap` parameter; default =
today's behavior). See design + experiment evidence in
`reference/self_notebook/specs/2026-06-16-bootstrap-profiles-design.md`.

## Upstream / Downstream

**Used at create time by:** `backend/routes/auth.py::create_agent` (the
`bootstrap` request field) and `ArenaProvisioningService` (the `arena` profile).
**`apply_bootstrap()` writes:** the agent workspace `Bootstrap.md` and merges
`{bootstrap_profile, bootstrap_greeting, bootstrap_auto_delete_after_events}`
into `agents.agent_metadata`.
**Read at runtime by:** `chat_module._resolve_bootstrap_greeting` + `GET /agents`
(the greeting), and `context_runtime` (the deletion threshold via
`auto_delete_threshold_from_meta`).
**Imports:** `bootstrap/template.py` (default content) and
`context_runtime/prompts.py` (`BOOTSTRAP_INJECTION_PROMPT`, the generic
injection prompt — kept global, Decision C).

## Design decisions

**Render-then-store, not resolve-at-runtime.** A profile is a *create-time*
concept. `apply_bootstrap()` renders everything into per-agent state, so the
runtime never needs the profile registry — it just reads metadata + the file.
This decouples the runtime and sidesteps registry-timing problems (the runtime
can't depend on the `arena` profile being imported). Updating an existing agent
later means re-`apply_bootstrap` (the separate propagation TODO).

**Per-profile deletion threshold (Decision B).** `auto_delete_after_events` is a
profile field stored in metadata; `None` means never rule-delete (semantic-only
— the agent deletes the doc itself per its instructions). Both built-in types
(`default`, `arena`) currently use `3`. `should_auto_delete(event_count)` is the
hook a profile can override for richer conditions later (Decision D).

**Backward compatible.** `auto_delete_threshold_from_meta` returns the historical
`3` when the metadata key is absent, so pre-profile agents behave unchanged.

## Gotchas

- The `arena` profile is registered as a side effect of importing
  `services/arena_provisioning_service.py`. The runtime never resolves it (it
  reads stored metadata), so the import-timing only matters at create time,
  where arena always imports its own module.
- `bootstrap_md()` returning `None` means "no Bootstrap.md" → `apply_bootstrap`
  removes any existing file and `bootstrap_active` becomes False.
- The injection prompt stays global (`context_runtime/prompts.py`); the profile
  has an `injection_prompt()` method as a future hook but the runtime does not
  read it per-agent.
