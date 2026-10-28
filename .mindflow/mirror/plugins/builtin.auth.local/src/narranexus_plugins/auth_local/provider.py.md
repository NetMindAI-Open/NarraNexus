---
code_file: plugins/builtin.auth.local/src/narranexus_plugins/auth_local/provider.py
last_verified: 2026-09-04
stub: false
---

# builtin.auth.local — provider.py

`LocalAuthProvider.authenticate(request)` returns `{user_id, role: "user", provider}` from the `X-User-Id` header or `None` when absent (the middleware decides per path what a missing identity means). `CONTRIBUTION` fills the one-arity slot `kernel.auth`; `HEADER` is the header name.
