---
code_file: backend/routes/agents_llm_config.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 (review fixes) — raw owner rows + deployment_mode + owner-scoped gate

- **GET reads RAW ``user_slots`` rows**, not ``UserProviderService.get_user_config().slots``.
  ``SlotConfig`` carries only provider_id / model / thinking / reasoning_effort —
  it DROPS the ``params_json`` and ``agent_framework`` columns ``_slot_view``
  reads. Feeding ``model_dump()`` made every owner-default framework read as
  claude_code and every reasoning param read as auto, breaking inheritance for
  codex_cli owners (the panel then wrote claude_code into the override → 400).
- ``_is_cloud`` was replaced by ``deployment_mode.is_cloud_mode()`` (single
  source of truth; honours ``NARRANEXUS_DEPLOYMENT_MODE`` + treats an unset
  ``DATABASE_URL`` as local) — the local ``_is_cloud`` DB-URL sniff was a 3rd
  copy of a known-skewed impl. ``providers.py``'s copy now delegates too.
- The staff-gate provider lookup is scoped to the OWNER
  (``{user_id, provider_id}``) so it never inspects another user's row.
- Route-level tests: ``tests/backend/test_agents_llm_config_routes.py`` (owner
  codex_cli default → GET returns codex_cli; non-owner 403; override view; reset).

## 2026-07-09 — per-agent LLM config endpoints

Sub-router (mounted under ``/api/agents`` via [[agents]]) for the per-agent LLM
overrides that back the chat-page model/framework quick-switch + detailed panel.
Delegates writes to [[agent_slot_service]].

Endpoints:
- ``GET /{agent_id}/llm-config`` — per-slot view for agent + helper:
  ``{inheriting, effective, override, owner_default}``. ``effective`` is the
  override if present else the owner default. The provider dropdown options come
  from the existing ``GET /api/providers`` (not duplicated here).
- ``PUT /{agent_id}/llm-config/{slot_name}`` — set one slot override.
- ``DELETE /{agent_id}/llm-config/{slot_name}`` — reset that slot to inherit;
  ``slot_name='all'`` clears both.

Design decisions / gotchas:
- **Ownership 403**: ``_require_owner`` asserts ``agents.created_by`` == the
  caller (``resolve_current_user_id``). Changing an agent's runtime brain/billing
  is owner-only.
- **Cloud staff-gate**: mirrors the framework-switch gate in [[providers]] — in
  cloud a non-staff caller may not bind an OAuth-source provider (it would ride
  the shared CLI credentials).
- **No hot-reload**: config is resolved per run from the DB, and
  ``set_user_config`` is ContextVar/task-scoped (can't reach a running loop), so a
  change here applies on the agent's NEXT run. The handler says so explicitly.
