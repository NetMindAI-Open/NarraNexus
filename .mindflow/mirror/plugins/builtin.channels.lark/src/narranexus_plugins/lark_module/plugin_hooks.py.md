---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/plugin_hooks.py
last_verified: 2026-09-04
stub: false
---

# lark_module/plugin_hooks.py — Manyfold credential export

## Intent

`onWillExportManagedChannels` → this channel's enabled bindings as the uniform rows the Manyfold inventory expects (the mapping moved verbatim from `backend/routes/manyfold/sync.py`; decoded secrets on purpose — the endpoint sits behind the gateway token). The route concatenates every channel's answer and pins the provider order; a disabled channel contributes nothing.
