---
code_dir: src/xyz_agent_context/agent_framework/provider_driver
last_verified: 2026-05-13
stub: false
---

# provider_driver — unified LLM provider abstraction

## Why this package exists

Before this package, NarraNexus had four kinds of LLM provider
(user-custom, aggregator quick-add, Claude OAuth, system free-tier
pool) sharing the same ``user_providers`` table but doing four
different things inside the resolver. There were also **two**
resolve paths — one for HTTP middleware (``ProviderResolver``) and
one for background triggers (``api_config._get_user_llm_configs_strict``)
— each with its own completeness checks. The result was the Xiong-class
bug: a slot.model that no longer exists in its provider.models array
slipping past every UI check and only failing inside a Lark trigger
hours later.

This package collapses both paths into one by giving every provider
type a Driver implementation. The resolver dispatches on the
``user_providers.driver_type`` column to the right Driver and asks
it to build a ``ClaudeConfig`` / ``OpenAIConfig`` / ``EmbeddingConfig``.
Anything specific to the provider type — auth header shape, OAuth
file path, system-pool quota deduction — lives inside the Driver.

## Submodules at a glance

| File | Job |
|---|---|
| ``base.py`` | ``ProviderCard`` dataclass + ``Driver`` Protocol + ``_DriverBase`` helper |
| ``registry.py`` | ``DRIVER_REGISTRY`` map + ``@register`` decorator |
| ``derive.py`` | Pure helpers: ``derive_driver_type`` / ``is_slot_broken`` / ``pick_default_model`` |
| ``backfill.py`` | One-shot migration of legacy ``user_providers`` rows (idempotent) |
| ``self_heal.py`` | Reverse-validation + auto-repair for broken slot bindings |
| ``resolver.py`` | The single-point ``resolve_user_llm_configs(user_id, db)`` |
| ``drivers/`` | One Driver class per provider kind |

## Lifecycle

1. **Boot** — ``backend.main`` runs ``auto_migrate`` (adds the new
   columns) then ``backfill_provider_metadata`` (fills derived values
   on legacy rows). Both are idempotent.
2. **Per call** — ``api_config._get_user_llm_configs_strict``
   delegates to ``resolve_user_llm_configs``. The resolver walks the
   three slots, runs self-heal on any broken binding (with 24h
   debounce + notification), and returns three configs.
3. **Post-call** — ``cost_tracker.record_cost`` is unchanged; the
   system-pool's billing hook lives on ``SystemDriver.on_call_completed``
   for the cloud-only path.

## Pointers to design docs

* Spec: ``reference/self_notebook/specs/2026-05-13-provider-unification-design.md``
* Visualisation (NarraNexus-deploy): ``reference/provider-system-tour.html``

## Open hooks for future phases

* SystemDriver registers conditionally — local mode skips it. Cloud
  migration creates a ``user_providers`` row with
  ``owner_user_id IS NULL`` so any user can opt in via slot binding.
* Per-slot system override (mixing "agent on system, embedding on
  user") is naturally supported by the slot→card model but the
  Settings UI hasn't shipped it yet; ``prefer_system_override`` is
  still the legacy switch.
* Sync-from-catalog (catalog evolves, append new models to user.models)
  is intentionally NOT auto-triggered by self-heal. It must remain
  user-initiated to respect deliberate deletions.
