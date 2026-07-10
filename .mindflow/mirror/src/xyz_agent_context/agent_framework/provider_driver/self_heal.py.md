---
code_file: src/xyz_agent_context/agent_framework/provider_driver/self_heal.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — table-aware writeback (agent_slots vs user_slots)

``self_heal_if_broken`` now heals into the RIGHT table: a per-agent override row
carries ``agent_id`` → repair ``agent_slots`` keyed by ``(agent_id, slot_name)``;
a plain user slot carries ``user_id`` → repair ``user_slots`` as before. Landed
WITH the resolver overlay ([[resolver]]) — without it, healing an overlaid
override would silently rewrite the user-level default it was shadowing. The
notification still goes to the owner (``card.user_id``).

# self_heal.py — reverse-validation + auto-repair

The Xiong-class bug: a ``user_slots.model`` no longer present in the
referenced ``user_providers.models`` array. The slot keeps firing
LLM calls, the provider keeps returning 4xx, the outer try/except
swallows the error, the user has no idea their PM agent stopped
working.

## Repair policy

1. Detect: ``slot.model NOT IN card.models``. Empty model is also
   "broken" (a misconfigured row).
2. Pick a safe default — first element of ``card.models``, or
   ``model_catalog.get_default_models(source, protocol)[0]`` fallback.
3. Write the new model back to the slot, set ``last_auto_repaired_at``.
4. Insert a ``user_notifications`` row of kind
   ``slot_auto_repaired`` so the user finds out at their next UI
   interaction.

## 24-hour debounce

``slot.last_auto_repaired_at`` is the cool-down clock. Within the
window we skip the repair (and the notification) and let the call
proceed with the already-rewritten model. This stops a misbehaving
slot from spamming the notification table once per LLM call. The
window is intentionally long — we want the user to see exactly one
notification per cause, not a stream.

## What we deliberately don't do

* **Sync-from-catalog is NOT triggered here.** Auto-pulling new
  models from the catalog into ``card.models`` would silently
  resurrect models the user just deliberately deleted. That belongs
  on a user-initiated "Sync available models" button.
* **No retry on the original LLM call.** We rewrite the slot for
  next time; this call is already in flight with the old model.
  The caller surfaces the error via the existing pipeline.
* **No catalog membership check on the slot.** A user who configured
  a private model on a forked endpoint should keep working — only the
  user's own card knowing about a model matters here.

## datetime gotcha

``_parse_dt`` accepts strings (SQLite return type), ``datetime`` (MySQL
via aiomysql) and ``None``. Don't replace this with ``datetime.fromisoformat``
directly — SQLite emits ``"2026-05-13 02:13:31.123456"`` (space, not
"T") which earlier Python versions reject.
