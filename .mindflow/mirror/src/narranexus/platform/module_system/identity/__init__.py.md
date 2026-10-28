---
code_file: src/narranexus/platform/module_system/identity/__init__.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

Public surface of the `module/identity/` package (blueprint P1 auth layer),
split by question: `tokens` answers "is this identity cryptographically
proven?", `mcp_auth` answers "what do we do about it?" (mode gating,
middleware, OwnerScopedPolicy). Re-exports the token primitives; middleware
and policy are imported from their own module (they are wiring, not a public
API for tools).

## Gotchas

- `module/_mcp_identity.py` deliberately stays OUTSIDE this package: it is the
  fail-open declared-identity channel with its own frozen bearer contract, and
  moving/renaming it would trigger the rule-#24 sweep for zero benefit. This
  package adds proof on top of the same headers.
