---
code_file: frontend/src/lib/netmindAuth/types.ts
last_verified: 2026-06-11
stub: false
---

# types.ts — NetMind auth shared type contracts

## Why it exists

Centralizes the TypeScript shapes returned by NetMind's auth API so that all
callers (`request.ts`, future hooks, stores) import from one place rather than
redeclaring the same inline types. Keeping types in their own file also means
they can be imported without pulling in any runtime code.

## What this file does NOT do

It does not contain any logic or runtime values — only `interface` declarations.
It does not define the request body shapes (those live inline at the call sites
in `request.ts` / hooks).

## Upstream / downstream

- **Used by**: `request.ts` (type parameter `T`), any hook or store that deals
  with the logged-in NetMind user or OAuth binding flow.
- **Depends on**: nothing (pure types, zero imports).

## Design decisions

- `NetmindUser` uses an index signature `[key: string]: unknown` to accommodate
  extra fields NetMind may add without breaking the TypeScript build.
- `AuthBindInfo.bandType` is kept as `number` (not a discriminated union) to
  match the raw API value; callers cast or switch on it themselves.

## Related constraints

- Iron Law #3 (module independence) — this file must not import from other
  netmindAuth modules.
