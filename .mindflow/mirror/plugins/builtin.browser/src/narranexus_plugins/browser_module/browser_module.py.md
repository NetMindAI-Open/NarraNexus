---
code_file: plugins/builtin.browser/src/narranexus_plugins/browser_module/browser_module.py
last_verified: 2026-09-22
stub: false
---

# BrowserModule

The optional capability contributes tools at the shared host's
`/mcp/browser_module` path. It does not own a dedicated port. Service/session
lifecycle and permission decisions remain in the platform browser subsystem.

The live hook is `gather`, followed by `contribute_turn_context`. The obsolete
`hook_data_gathering` name was never called, and an extra_data entry alone was
never rendered. The module now collects runtime readiness and persisted origin
settings and renders them into the runtime's volatile turn context. Stable
instructions remain cacheable. Runtime probe failures are reported as unknown,
not mistaken for an absent installation; policy failures are logged separately.

Profiles are persistent and isolated per agent. The module does not infer that
a persisted profile is logged in: the current page must establish that fact.
Configured policies omit stored turn/thread grants belonging to other calls.

Private tool handlers share one dispatcher. Every call reads the platform
bearer, binds the current event and conversation on the live session, and runs
inside an awaited task so ContextVar bindings never outlive the call. Opening
passes the same scope into BrowserService; reusing a session does not reuse its
first caller's grant. The browser service owns policy evaluation, callbacks and
operation serialization. Missing runtime, missing scope, closed sessions and
transport failures produce actionable outcome envelopes.

Human login returns a panel handoff request; only the user takes control.
Screenshot evidence goes through the existing workspace and ArtifactService.
There is no arbitrary tool-count limit, per-click API, or site-specific flow.

Integration tests: `tests/module/test_browser_module_integration.py` exercises
real ContextRuntime assembly, instrumented MCP dispatch, BrowserService,
BrowserSession, policy lifetimes, concurrency and artifact registration.
