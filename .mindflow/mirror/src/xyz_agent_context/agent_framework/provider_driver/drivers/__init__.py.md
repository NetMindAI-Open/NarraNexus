---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/__init__.py
last_verified: 2026-05-13
stub: false
---

# drivers/__init__.py — import-time registration

Imports each Driver module so its ``@register`` decorator fires. Order
of imports inside this file is irrelevant — every Driver maps to a
distinct ``driver_type`` so there's no last-write-wins collision.

``system.py`` is included unconditionally; the cloud-mode gate is
inside that module so a local install gets the import but not the
registration. This means a future "elevate this local instance to
cloud" toggle wouldn't need a different bootstrap path.
