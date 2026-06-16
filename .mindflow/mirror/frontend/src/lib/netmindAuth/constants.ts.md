---
code_file: frontend/src/lib/netmindAuth/constants.ts
last_verified: 2026-06-11
stub: false
---

# constants.ts — NetMind auth common request parameters

## Why it exists

NetMind's auth API requires the same boilerplate fields (`deviceId`,
`clientType`, `clientVersion`, `sysCode`) on every request. This file
centralises that construction so callers only provide the endpoint-specific
fields. `sysCode` is injected at runtime from `getNetmindConfig()` rather than
hardcoded so the same bundle works across dev and prod deployments.

## What this file does NOT do

It does not build or send requests — that is `request.ts`. It does not hold the
API base URL — that comes from `runtimeConfig.getNetmindConfig().authApi`.

## Upstream / downstream

- **Used by**: any function that assembles a NetMind API request body (e.g.
  `emailLogin` in the auth hook will spread `baseRequestParams()` into the body
  object before calling `netmindPost`).
- **Depends on**: `@/lib/runtimeConfig` for `getNetmindConfig()`.

## Design decisions

- `baseRequestParams` is a function (not a frozen object constant) so that
  `sysCode` is always read fresh from the runtime config; this matters during
  tests where `window.__NARRANEXUS_CONFIG__` may be set after module load.

## Related constraints

- Iron Law #3 (module independence) — must not import from other netmindAuth
  modules.
