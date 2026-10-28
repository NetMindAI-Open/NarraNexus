---
code_file: src/narranexus/platform/browser/_browser_impl/stream_auth.py
last_verified: 2026-09-23
stub: false
---

# Internal relay authentication

The public backend authenticates the user and owns the authorization decision;
this module proves that a connection to the MCP stream came from that backend.
Signatures bind the agent ID and a short connection-establishment expiry.
They do not cap how long an already authenticated browser session may run.

Local processes share a 0600 random key atomically published under
~/.narranexus/browser-auth, overridable with NARRANEXUS_BROWSER_AUTH_DIR.
Cloud backend and MCP processes must share NARRANEXUS_BROWSER_STREAM_SECRET
(at least 32 characters). Cloud has no default secret. The frontend never sees
this key or sends this internal header, and arbitrary webpage origins are denied
by stream_bridge even if they can reach the module host port.
