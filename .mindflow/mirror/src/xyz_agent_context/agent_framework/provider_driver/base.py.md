---
code_file: src/xyz_agent_context/agent_framework/provider_driver/base.py
last_verified: 2026-05-13
stub: false
---

# base.py — ProviderCard + Driver Protocol

## ProviderCard

In-memory snapshot of one ``user_providers`` row. Frozen dataclass so a
Driver instance can hand it around without anyone accidentally mutating
the source. ``from_row`` does the heavy lifting on JSON-text ``models``
and tolerates legacy rows where the Phase-0 columns are still null
(self-heal / resolver fall through to fallback paths in that case).

## Driver Protocol

``typing.Protocol`` instead of an ABC. Three reasons:
1. Future third-party drivers can duck-type without inheriting from us.
2. Tests can construct stub drivers without forwarding ``__init__``.
3. ``_DriverBase`` carries shared defaults (``models``, ``probe``,
   ``on_call_completed``, NotImplementedError stubs for the three
   ``build_*_config`` methods) so concrete drivers stay tiny.

## CallContext

Drop-in info for the post-call hook. Only ``SystemDriver`` actually
reads it; other drivers ignore the argument. Kept frozen so it can't
be mutated mid-flight between cost_tracker layers.

## DriverHealth

Output of ``probe()``. Three fields, all optional except ``ok``.
``expires_at`` is here for the OAuth driver's TTL surfacing — other
drivers leave it as ``None``.
