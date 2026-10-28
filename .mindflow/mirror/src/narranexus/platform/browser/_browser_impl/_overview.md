---
code_dir: src/narranexus/platform/browser/_browser_impl/
last_verified: 2026-10-08
---

# Browser implementation boundary

The session combines the owner's script-permission policy, exclusive control
and frame subscriptions. CDP owns protocol liveness, while runtime_launch owns
the process. PolicyStore writes permissions with a per-agent lock plus a
cross-process compare-and-swap; stream_auth proves the backend is the caller of
the internal stream. `limits` bounds every model-supplied argument (selectors,
field text, scripts) before it is embedded in a CDP message; `visual` owns the
screenshot geometry for browser_look.

Runtime discovery and installation have their own coordinator and download
implementation. Callers should use BrowserService rather than combine private
objects or create an independent session registry.
