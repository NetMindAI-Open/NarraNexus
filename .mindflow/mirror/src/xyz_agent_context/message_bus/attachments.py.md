---
code_file: src/xyz_agent_context/message_bus/attachments.py
last_verified: 2026-07-22
stub: false
---

# attachments.py — public facade over _bus_attachment_impl

## Why it exists

Backend routes ([[teams]], [[inbox]], [[transcription_public]]) and the module
MCP tools need attachment staging/resolution, but the project layering
(api → service protocol → private impl) forbids cross-package imports of
underscore-private modules — PR #141 review found three route files pinned
directly to ``_bus_attachment_impl``. This module is a pure re-export facade:
zero logic, just the sanctioned public names. Cross-package consumers import
from HERE; ``_bus_attachment_impl`` stays free to reorganize internally.
In-package consumers ([[message_bus_trigger]]) may keep importing the private
module directly.
