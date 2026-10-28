---
code_file: frontend/src/components/bookmarks/__tests__/panelsStrip.test.tsx
last_verified: 2026-09-23
stub: false
---

# Registry-Derived Panel Strip

Checks that strip metadata determines category order and visibility for builtins
and plugin contributions. Browser appears in Activity while remaining owned by
`builtin.browser`, so the shell-only `builtinTabIds()` helper excludes it. This
prevents a navigation assertion from accidentally requiring permanent shell
ownership for a feature that users can disable.
