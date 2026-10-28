---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/plugin_hooks.py
last_verified: 2026-09-04
stub: false
---

# chat_module/plugin_hooks.py — builtin.chat's `backend.hooks`

## Intent

Step 1 of a bootstrapping agent's first turn used to import `seed_bootstrap_greeting` from the chat module — the platform reaching into a builtin. It now fires the host event `onDidResolveBootstrapGreeting` (payload in `contracts/events.py`) and this module implements it: fetch the DB client, seed the greeting idempotently into the head chat instance. With builtin.chat disabled the hook has no implementation and step 1 carries on; the platform holds no chat-history knowledge.

Registered twice on purpose — at import by `module/contributions.register_all` (processes that never run a manifest boot, tests) and by the manifest loader — which `HookCaller.add` collapses into one implementation (same fn + owner).
