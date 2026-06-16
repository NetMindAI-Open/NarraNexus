---
code_file: frontend/src/lib/netmindAuth/request.ts
last_verified: 2026-06-11
stub: false
---

# request.ts — NetMind auth API fetch wrapper

## Why it exists

NetMind's auth API has three non-standard conventions that every caller would
otherwise need to re-implement:

1. Body must be `application/x-www-form-urlencoded` (not JSON).
2. Auth token is sent in a literal header named `token` (not `Authorization`).
3. HTTP 200 responses can still represent failures — the `{success, data, msg}`
   envelope must be unwrapped and `success: false` must raise an error.

This file wraps those three concerns in one place so higher-level callers (hooks,
services) deal only with typed inputs and outputs.

## What this file does NOT do

It does not inject the common boilerplate params (`deviceId`, `clientType`,
etc.) — that is `constants.ts`. It does not encrypt passwords — that is
`crypto.ts`. It does not handle OAuth popup messaging — that belongs in the
OAuth hook.

## Upstream / downstream

- **Used by**: the NetMind auth hook (`useNetmindAuth.ts`, planned) which calls
  `netmindPost` for `emailLogin`, `sendCode`, `userCallBack`, and any other
  endpoint.
- **Depends on**: `@/lib/runtimeConfig` (`getNetmindConfig`) for the `authApi`
  base URL.

## Design decisions

- **`token` header, not `Authorization`**: NetMind's server-side code reads the
  literal header name `token`. Using `Authorization: Bearer …` would silently
  fail (401 or empty user). The header name is intentional, not a bug.
- **Form-urlencoded, not JSON**: NetMind's auth endpoints were built for a
  mobile/web client that posts forms. JSON bodies are not accepted.
- **Envelope unwrap on success:false**: a 200 OK with `{success:false}` is a
  business-layer error (wrong password, expired code, etc.). Throwing here lets
  callers use a single `catch` branch instead of checking the envelope manually.

## Gotcha / boundary cases

- **Trigger**: if the runtime config `netmindAuthApi` is not set (e.g. local
  dev with no `/config.js`), `authApi` is `""` and `fetch` will hit a relative
  path like `/user/emailLogin`.
  **Symptom**: 404 or same-origin request to the NarraNexus backend, not
  NetMind.
  **Root cause**: `getNetmindConfig()` returns `""` when the key is absent; no
  guard is applied in `netmindPost` itself (the caller is expected to check
  `authApi` before proceeding, or the deploy pipeline must set the key).

- **Trigger**: passing `success: undefined` in a mock (not `true` and not
  `false`).
  **Symptom**: `netmindPost` resolves normally and returns `json.data`.
  **Root cause**: the guard is `=== false` (strict); `undefined` is not
  `false`. This is intentional — some older NetMind endpoints omit `success`
  on success.

## Related constraints

- Iron Law #3 (module independence) — must not import from other netmindAuth
  modules (types are allowed as they are zero-runtime).
- See `references/phase1-frontend-login-migration.md` §3 for the full request
  layer design and `token` header convention source.
