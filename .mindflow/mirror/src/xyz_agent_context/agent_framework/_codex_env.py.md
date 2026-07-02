---
code_file: src/xyz_agent_context/agent_framework/_codex_env.py
stub: false
last_verified: 2026-06-17
---

## Why it exists

Both codex spawn paths (`xyz_codex_official_sdk` v2 and
`xyz_codex_cli_sdk` v1) used to build the subprocess environment as
`{**os.environ}` — handing the codex child the backend container's
**entire** environment. The backend env carries every platform secret
(`DB_PASSWORD`, `JWT_SECRET`, `ADMIN_SECRET_KEY`, `*_API_KEY`,
`*_SECRET`, ...), so any agent that ran `env` / `printenv` / read
`/proc/self/environ` in its workspace exfiltrated all of them
(**incident 2026-06-17**).

This module inverts the default: instead of "inherit everything, blank a
few", `build_codex_subprocess_env` passes an explicit **allowlist** of
the handful of variables codex needs, and nothing else.

## Key design decisions

- **Allowlist, not denylist.** A denylist rots (binding rule #8): every
  new secret added to `.env` would leak until someone remembered to
  blank it. With an allowlist, new secrets are safe **by default** —
  they never reach the subprocess unless deliberately added here.
- **A filesystem sandbox does NOT solve this.** `env` reads the
  process's own memory, not the filesystem — so codex's `workspace-write`
  sandbox (or any uid/namespace FS isolation) is irrelevant to the env
  leak. This is the env half of the fix; FS isolation is a separate
  concern (root cause ②).
- **The one allowed secret is the scoped LLM key.** Codex must
  authenticate to the model, so the agent's own credential reaches it —
  but explicitly via `cli_env` (`CodexConfig.to_cli_env` →
  `CODEX_API_KEY`), never via `os.environ` passthrough. That key should
  be a per-user, rotatable, least-privilege token (not the shared
  `SYSTEM_DEFAULT_*` master key).
- **Allowlist contents.** System basics (`PATH`/`HOME`/`USER`/`SHELL`),
  terminal/timezone, locale (`LANG` + `LC_*` by prefix), temp dirs,
  TLS trust material (`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`/
  `NODE_EXTRA_CA_CERTS`/...), and outbound proxy vars
  (`HTTP(S)_PROXY`/`ALL_PROXY`, both cases). `NO_PROXY` is set
  explicitly (MCP is local) rather than inherited.
- **Layering (later wins):** allowlisted os.environ → `CODEX_HOME` +
  `NO_PROXY` → `cli_env` (scoped key) → `extra_env` (per-call override).
- **Never mutates `os.environ`** — returns a fresh dict.

## Gotchas

- A stray `CODEX_API_KEY` already present in the parent env is **not**
  inherited (not on the allowlist); only the `cli_env` one is
  authoritative — closing the cross-tenant key-leak the old
  `to_cli_env` "explicit blank" trick used to guard.
- This covers only the **codex** paths. The Claude path
  (`xyz_claude_agent_sdk`) merges `{**os.environ, **options.env}`
  *inside* the SDK transport, so its env fix needs a different lever
  (custom transport / `cli_path` wrapper / boot-time scrub) — tracked
  under the broader agent-isolation work.
