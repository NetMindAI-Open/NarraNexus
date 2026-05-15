---
code_file: src/xyz_agent_context/agent_framework/provider_driver/resolver.py
last_verified: 2026-05-13
stub: false
---

# resolver.py — single-point LLM config resolution

## Why this exists

The codebase had two parallel resolve paths — ``ProviderResolver`` for
HTTP middleware and ``api_config._get_user_llm_configs_strict`` for
background triggers. Each had its own completeness check and error
handling, drift between them caused real bugs (Xiong's case shipped
because the frontend Settings check ran the HTTP path while the LarkTrigger
that broke ran the background path). This module collapses both into one
function and ``api_config._get_user_llm_configs_strict`` now delegates
to it. ``ProviderResolver.resolve_and_set`` will follow in Phase 2.

## Pipeline

```text
user_id
  └─ db.get('user_slots', user_id=user_id)  → 3 rows expected
       └─ for each slot:
            db.get_one('user_providers', provider_id=...)
              ├─ visibility check (owner_user_id matches OR is null)
              ├─ self_heal_if_broken (rewrites slot.model if needed)
              ├─ on-the-fly driver_type derive if backfill hasn't run yet
              ├─ DRIVER_REGISTRY[driver_type] → Driver instance
              └─ driver.build_<kind>_config(slot.model)
```

## Visibility rule

A card is visible if ``owner_user_id == user_id`` OR
``owner_user_id IS NULL``. The null case covers two things at once:

* Cloud system-shared cards (admin created the row with null owner).
* Legacy rows that pre-date the Phase 0 ``owner_user_id`` column —
  for those we fall back to ``card.user_id == user_id`` so the
  resolver doesn't refuse old data on first boot.

## Errors

Every failure raises ``LLMConfigNotConfigured`` with an actionable
message. The caller's UX layer surfaces it to the user. No silent
fallback to a different account — that was a leading cause of
billing surprises in the old code.
