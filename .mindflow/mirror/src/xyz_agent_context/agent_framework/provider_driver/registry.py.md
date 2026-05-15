---
code_file: src/xyz_agent_context/agent_framework/provider_driver/registry.py
last_verified: 2026-05-13
stub: false
---

# registry.py — driver_type → class map

Module-level ``dict`` populated by the ``@register`` decorator at import
time. The resolver consults it via ``get_driver_class(driver_type)``;
unknown keys return ``None`` and the resolver raises
``LLMConfigNotConfigured`` — that's intentionally loud so a misconfigured
row never silently routes to a default that bills the wrong account.

Re-registering the same class is a no-op (idempotent). Re-registering
a different class under the same key logs a warning and overwrites —
this only triggers under test fixtures that monkeypatch drivers; in
production every driver registers exactly once.

``SystemDriver`` doesn't use the decorator directly; instead its module
calls ``register(SystemDriver)`` inside an ``if is_cloud_mode():``
block. Local installs never see it in the registry, which means a
``driver_type='system_pool'`` row on a local DB raises the loud error
above instead of half-working.
