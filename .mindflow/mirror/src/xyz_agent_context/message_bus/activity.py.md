---
code_file: src/xyz_agent_context/message_bus/activity.py
last_verified: 2026-07-22
stub: false
---

# activity.py — public read surface for team-room live activity

## Why it exists

Same layering rationale as [[attachments]]: the team-chat GET route needs
``get_channel_activity`` / ``is_live`` but must not import the private
[[_bus_activity]] module cross-package. Only the READ side is re-exported —
the write side (``mark_running`` / ``update_phase`` / ``mark_idle``) belongs
exclusively to [[message_bus_trigger]] inside the package and stays private
on purpose: no route should ever fabricate an agent's live status.
