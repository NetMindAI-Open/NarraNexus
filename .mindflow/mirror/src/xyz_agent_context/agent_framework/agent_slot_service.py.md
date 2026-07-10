---
code_file: src/xyz_agent_context/agent_framework/agent_slot_service.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — per-agent slot override writer

Writer/reader for the ``agent_slots`` table (the overlay itself lives in
[[resolver]]). An agent inherits its owner's user-level slots by default; this
service upserts/reads the optional per-agent overrides that let one agent pin its
own coding-agent framework + model (agent slot) and its own helper model
(helper_llm slot), independent of the owner default and of the owner's other
agents.

Why it exists as its own service (not more methods on ``UserProviderService``):
the two writers have different scopes (user vs agent) and different key columns,
but MUST enforce the same provider↔slot binding rules — so the rules live in the
shared ``user_provider_service.validate_slot_binding`` and both call it. Without
that, a per-agent override could bind an incompatible provider (e.g. a codex_cli
agent slot on an aggregator, or a helper slot on an OAuth card) and the misbinding
would only surface at agent-loop time as a cryptic NotImplementedError.

Gotchas:
- The provider must belong to the agent's OWNER (providers are user-scoped);
  ``set_agent_slot`` resolves the owner from ``agents.created_by`` and looks the
  provider up under that user.
- Only the agent slot carries a framework. For the agent slot, a per-agent
  framework (if given) is validated against; else it falls back to the owner's
  current framework.
- ``clear_agent_slot(slot_name=None)`` deletes ALL of the agent's overrides (full
  reset to inherit); a specific ``slot_name`` resets just that slot.
- Only the own-provider resolution path honours overrides; the cloud SYSTEM
  free-tier pool ignores them (fixed one-model config).
