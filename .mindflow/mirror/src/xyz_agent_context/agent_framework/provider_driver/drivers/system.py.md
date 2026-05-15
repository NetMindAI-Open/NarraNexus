---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/system.py
last_verified: 2026-05-13
stub: false
---

# system.py — cloud-only system free-tier pool driver

The only Driver registered conditionally — guarded by
``is_cloud_mode()``. Local DMG / ``bash run.sh`` installs skip the
``register()`` call, so a misconfigured row with
``driver_type='system_pool'`` on a local DB raises a loud
``LLMConfigNotConfigured`` in the resolver instead of half-working.

## What's different from user-pays drivers

The only behavioural divergence is ``on_call_completed``: after every
successful LLM call we call ``QuotaService.deduct(user_id, in, out)``
so the user's free-tier budget ticks down. ``cost_records`` is still
written by the regular ``cost_tracker.record_cost`` path — the
``billing_policy='system_quota'`` flag on the card is what tells
cost_tracker (in a future Phase 1.5) to additionally deduct.

Deduct failure is logged but never raised. The user-facing LLM call
already succeeded; failing the request because an accounting write
hiccupped would be the wrong trade-off.

## Where the credential comes from

Cloud migration (Phase 3) inserts a ``user_providers`` row with
``owner_user_id IS NULL`` and ``driver_type='system_pool'``, copying
the values from the existing ``SYSTEM_DEFAULT_LLM_*`` env vars.
Once that's in place, any user whose slot binding points at this
row routes through SystemDriver.
