---
code_file: src/xyz_agent_context/agent_framework/provider_driver/derive.py
last_verified: 2026-05-13
stub: false
---

# derive.py — pure helpers

## Why these functions live outside Driver classes

* ``backfill`` runs them against raw DB rows that aren't yet
  classified — Driver instances don't exist there.
* ``self_heal`` needs the "is this slot broken / what's a safe
  default" decision before it has decided which Driver to use.
* Tests can table-drive them without DB fixtures.

Keeping them in a separate module enforces that the logic is pure —
no DB calls, no side effects, no provider lookups.

## derive_driver_type

The truth table sits in the docstring. Two gotchas:

* ``user`` source maps to ``custom_anthropic`` / ``custom_openai`` —
  the legacy ``source='user'`` ProviderSource enum is intentionally
  ambiguous about protocol, so we disambiguate here.
* ``system`` source maps to ``system_pool`` — the corresponding
  Driver is cloud-only. Backfill on a local DB will never see a
  ``system`` source row because there's no UI to create one.

## derive_billing_policy

Three values: ``user_pays`` (default), ``system_quota`` (cloud
system pool), ``external_oauth`` (Claude OAuth — Anthropic does the
billing on their side). ``cost_tracker`` reads ``billing_policy``
post-call to decide whether to deduct from ``user_quotas``.

## derive_auth_ref + resolve_claude_credentials_path

OAuth rows store a sentinel string ``claude-cli:~/.claude/.credentials.json``
in ``auth_ref``. ``resolve_claude_credentials_path`` expands it at
use-time, respecting ``CLAUDE_CLI_HOME`` / ``CLAUDE_CLI_CREDENTIALS_PATH``
env vars so admins can relocate the credentials file (or tests can
inject a fake one).

## is_slot_broken + pick_default_model

The key business rule: the check is against the **card's own**
``models`` array, not against the global catalog. A user who configured
a private model that we don't recognise is fine; only a slot whose
model isn't in its own provider's list is broken.

``pick_default_model`` prefers the first element of card.models, falls
back to ``model_catalog.get_default_models(source, protocol)[0]``,
returns ``None`` if both are empty (caller logs and lets the call fail).
