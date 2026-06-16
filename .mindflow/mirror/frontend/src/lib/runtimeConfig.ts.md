---
code_file: frontend/src/lib/runtimeConfig.ts
last_verified: 2026-06-11
stub: false
---

# runtimeConfig.ts — runtime deploy-time config, injected via /config.js

## 2026-06-11 — NetMind endpoint config keys

Added `NetmindConfig` interface and `getNetmindConfig()` function to expose four
NetMind-specific endpoints that vary between dev and prod environments:
`authApi`, `accountsUrl`, `sysCode`, `registerUrl`. These are injected at deploy
time via `window.__NARRANEXUS_CONFIG__` (same mechanism as `mode`/`apiUrl`) so
one built bundle can serve multiple environments without rebuilding.

## Why this file exists

The frontend is built once and deployed to many environments (local, dev EC2,
prod EC2, future tenants). Build-time env vars (VITE_*) would require a
separate build per environment. Instead, the deploy pipeline writes a `/config.js`
before nginx boots, populating `window.__NARRANEXUS_CONFIG__` with the correct
values for that deployment. This file is the single read point for all such
runtime-injected config.

## This file does NOT do

- It does not store user preferences or session state.
- It does not handle feature flags (those are separate).
- It does not validate that injected values are reachable — it only reads and
  normalises them.

## Upstream / downstream

- **Injected by**: deploy pipeline entrypoint script writes `/config.js` which
  runs before the Vite bundle loads.
- **Read by**: any module that needs to know the deployment mode (`isForcedCloud`,
  `isForcedLocal`) or NetMind endpoint URLs (`getNetmindConfig`). Currently the
  main consumers are auth hooks in `frontend/src/lib/netmindAuth/`.

## Design decisions

- **Runtime injection over build-time VITE_**: one image, many deployments; no
  rebuild required when switching environments.
- **Trailing slash stripping in `_str`**: NetMind API docs show URLs with and
  without trailing slashes; normalising here means callers always get a clean
  base URL they can append paths to with a single `/`.
- **`sysCode` does NOT strip trailing slash**: it is an opaque token, not a URL;
  the `_str` helper is intentionally not used for it.

## Gotcha / edge cases

- **Trigger**: when `window.__NARRANEXUS_CONFIG__` is not set at all (local dev
  without a config.js). **Symptom**: all getters return empty strings / null.
  **Root cause**: the guard `|| {}` returns an empty object, so every key
  resolves to `undefined`, and the type-narrowing falls back to `''`.
- **Trigger**: when a deployer sets `netmindAuthApi` with a trailing slash (e.g.
  `https://auth.example.com/`). **Symptom**: would double-slash any path
  appended by callers. **Root cause**: fixed by `_str` which strips trailing
  slashes from all string-type URL fields.

## Related constraints

- Iron rule #10 — mirror md must be updated in the same commit as any
  behavioural change to this file.
