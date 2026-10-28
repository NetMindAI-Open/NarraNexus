---
code_file: src/narranexus/platform/module_system/identity/tokens.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The cryptographic core of MCP caller auth (blueprint P1, Q1/Q2). The
`_mcp_identity.py` header channel is self-declared and forgeable; this module
provides the proof: a short-lived Ed25519 (EdDSA) JWT bound to `sub=user_id` +
`exp`. Private key stays with the issuer only — cloud: the executor broker
(deploy repo signs at ensure() time), local: this process via
`LocalEphemeralIssuer` — verifiers (module MCP servers, backend service path)
hold just the public key, so a compromised verifier cannot mint identities.

## Design decisions

- **No agent_id in the token.** Ownership is resolved per tool call
  (OwnerScopedPolicy in mcp_auth.py); one token covers all of a user's agents,
  and the broker — which knows only user_id — can sign it.
- **Algorithm pinned to EdDSA.** Never widen `algorithms=` to include HS*;
  that re-opens the algorithm-confusion forgery (public key as HMAC secret) —
  there is a test hand-rolling that exact attack.
- **`nx: identity` kind claim** so a same-key JWT minted for another purpose
  can never pass as a caller identity.
- **TTL 72h default** (`NX_IDENTITY_TOKEN_TTL_SECONDS`): minted per run,
  never refreshed mid-run, and runs are unbounded (iron rule #14) — the TTL
  must outlive the longest run. Audit-phase logs decide the final value.
  **Q6 widening**: the same token is also a user-level backend API credential
  (nx-agent service path; role "user", no elevation, no revocation list) —
  the TTL decision weighs BOTH planes, not just MCP.
- **`load_public_key_pem()` never raises**; None = not provisioned. Each
  verifier chooses its own degradation (mcp fails open + warning, backend
  service path fails closed).
- mtime-keyed pubkey cache so a re-published local key is picked up without
  restarting mcp; steady state costs one stat().

## Gotchas

- `LocalEphemeralIssuer` touches the filesystem only on the first
  `token_for()` (publishes the public key atomically to
  `~/.narranexus/identity/`, overridable via `NX_IDENTITY_KEY_DIR`). Callers
  gate on `NX_MCP_AUTH_MODE != off`, which is what keeps a default local run
  byte-identical (iron rule #7). The private key never touches disk.
- Tokens ride the nx-agent bearer as positional field 7 — JWT's charset has
  no `~`, which is load-bearing (test-pinned).
