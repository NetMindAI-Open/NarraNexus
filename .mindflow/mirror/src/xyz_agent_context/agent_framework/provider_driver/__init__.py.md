---
code_file: src/xyz_agent_context/agent_framework/provider_driver/__init__.py
last_verified: 2026-05-13
stub: false
---

# __init__.py — package facade

Re-exports the public API so callers can write
``from xyz_agent_context.agent_framework.provider_driver import resolve_user_llm_configs``
without reaching into submodules. Imports ``drivers`` to trigger
``@register`` side effects — by the time the resolver runs the registry
is populated.

The import order matters only in one way: registry must be imported
before drivers (so the decorator exists). Python's module loader
gives us that for free because ``drivers/__init__.py`` imports
``provider_driver.registry`` transitively via ``base``.
