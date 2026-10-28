---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# basic_info_module/__init__.py — package surface

Was empty; PR-7 gave it a public surface. It re-exports the dialect-safe
narrative/event read helpers from [[_narrative_reads]] — `fetch_narrative_view`
/ `fetch_event_view` / `check_narrative_switch` / `narrative_chat_history` — so
the AgentDataStore seam's DirectStore ([[store]]) and the backend narrative
routes ([[narrative]]) import the PACKAGE, not the private `_narrative_reads`
leaf. Making the shared contract the package's public face keeps "who is the
shared contract vs an internal implementation detail" self-evident at the
import boundary (same precedent as social_network_module's `__init__`).
