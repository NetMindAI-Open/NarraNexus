---
code_dir: src/narranexus/platform/browser/
last_verified: 2026-10-08
---

# Browser platform

The platform owns runtime readiness, one session per agent, the owner's script
permission and the authenticated frame transport. The browser plugin contributes
MCP tools and binds each call's runtime-declared scope (attribution, not
authorization); the backend route authenticates human viewers. Desktop / local
distributions only: the cloud stack cannot share the runtime between its backend
and mcp containers (PR #410 review C2).
Installer implementations remain independent of the session and transport logic.

BrowserService is the common facade. stream_bridge serves sessions in their
owning process. Private implementations handle control, CDP, policy, input limits
and process cleanup. See the individual mirrors for the deliberately limited
permission guarantees around arbitrary page JavaScript.

The package `__main__` is the manual installer entrypoint. It delegates to
`_browser_impl/install.py` and is included in the engine wheel so recovery works
with the desktop's bundled Python as well as a source checkout.
