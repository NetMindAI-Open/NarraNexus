---
code_file: frontend/scripts/sync_workspace_versions.mjs
last_verified: 2026-09-07
stub: false
---

# scripts/sync_workspace_versions.mjs — keeps the three publishable packages' versions in lockstep with the app

## Why it exists

`@narranexus/sdk`, `@narranexus/ui-kit` and `@narranexus/chat-widget` are separate npm packages,
but they ship alongside the app and describe its HostAPI / embedding surface AT that release.
Nothing previously kept their `package.json` `"version"` in sync with `frontend/package.json`'s
own version — letting them drift is how `@narranexus/sdk` on npm ends up describing a HostAPI
shape from a different release than its version number suggests (I-10). This script is the fix:
copy the app version into all three on demand.

## This file doesn't do

It does not run automatically as part of `npm install` or CI — it is a release-time step (see the
deploy repo's release SOP), invoked via `npm run sync:versions` (added to `frontend/package.json`
in the same change).

## Upstream / Downstream

- **Used by**: the release process, run manually (or from a release script) after bumping
  `frontend/package.json`'s version, before publishing the three workspace packages.
- **Verified by**: `scripts/__tests__/workspaceVersions.test.ts`, which asserts the three package
  versions actually match the app version and that `chat-widget`'s `@narranexus/ui-kit` dependency
  is a real semver range, not `"*"` (I-10's second half — see `packages/chat-widget/package.json`).

## Design decisions

- Read-modify-write each target `package.json` individually rather than a blanket
  find-and-replace, so any future field ordering / formatting quirks in a given package.json are
  preserved except for the one field that changed.
