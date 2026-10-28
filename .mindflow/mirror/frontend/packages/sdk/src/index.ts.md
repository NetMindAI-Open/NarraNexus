---
code_file: frontend/packages/sdk/src/index.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — VitePresetResult removed (C-3)

`vitePreset()` returns a real Vite `Plugin` now, not plain build-config data — `VitePresetResult`
no longer exists. `VitePresetOptions` is unchanged.

# @narranexus/sdk — index.ts

Build-time surface of the SDK package (batch 6d): `definePlugin`, `vitePreset`/`HOST_EXTERNALS`/`hostShimModule`, and the HostAPI/registry types (`types.ts`). The app's `src/sdk/index.ts` is the runtime shim for the same specifier and adds the shared components.
