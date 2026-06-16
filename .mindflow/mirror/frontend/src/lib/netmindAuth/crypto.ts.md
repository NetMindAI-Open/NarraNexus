---
code_file: frontend/src/lib/netmindAuth/crypto.ts
last_verified: 2026-06-11
stub: false
---

# crypto.ts — NetMind DES password encryption

## Why it exists

NetMind's `emailLogin` API requires the password to be encrypted with DES-CBC
before transmission. This is a protocol-level requirement inherited from
NetMind's existing clients (Arena does the same). The browser's built-in
Web Crypto API does not support DES (it was dropped as a weak cipher), so
`crypto-js` is the only viable path without a custom WASM build.

This file is isolated from the rest of netmindAuth so the DES dependency is
contained: if NetMind ever upgrades to AES or a stronger scheme, only this
file changes.

## What this file does NOT do

It does not generate the `signStr` that becomes the DES key — that is the
caller's responsibility (`generateRandomString` is provided here as a
convenience because it is always used together with `encryptPassword`).
It does not send any network request; that is `request.ts`.

## Upstream / downstream

- **Used by**: `useNetmindAuth.ts` — `emailLogin()` calls
  `generateRandomString(8)` to get a fresh `signStr`, then
  `encryptPassword(password, signStr)` to produce the ciphertext before
  posting to `emailLogin`.
- **Depends on**: `crypto-js` (runtime) + `@types/crypto-js` (dev).

## Design decisions

- **Key === IV**: DES-CBC requires separate key and IV. Arena's protocol sets
  IV = key = signStr. This is intentional (protocol compat), not a security
  oversight in our code.
- **PKCS7 padding, hex output**: matches Arena's `CryptoJS.DES.encrypt` call
  verbatim. Any deviation breaks the server-side decryption.
- **No default key in production paths**: `encryptPassword` has a default
  `key = '01234567'` only to keep the function signature ergonomic for tests.
  Real callers always pass a freshly generated `signStr`.

## Gotcha

- **Trigger**: if you pass a `key` shorter than 8 bytes (e.g. `'abc'`)
  **Symptom**: CryptoJS silently pads the key with null bytes, producing a
  ciphertext the server cannot decrypt (no runtime error thrown)
  **Root cause**: DES key must be exactly 8 bytes; CryptoJS's `Utf8.parse`
  does not validate length

## Related constraints

- Iron Law #2 (no backward compat) — do not keep the old self-built auth path
  running in parallel once netmindAuth is wired in
- See `references/phase1-frontend-login-migration.md` §3 for the full module
  map and why crypto-js was chosen over Web Crypto
