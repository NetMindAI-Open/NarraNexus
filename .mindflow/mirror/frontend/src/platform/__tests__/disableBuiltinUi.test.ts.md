---
code_file: frontend/src/platform/__tests__/disableBuiltinUi.test.ts
last_verified: 2026-10-14
stub: false
---

# Builtin feature UI lifecycle

Feature-level UI contributions must belong to their plugin ID so the loader can
remove them together, while protected shell entries that the running distribution does NOT exclude remain. Tests cover the
existing teams route contributions and browser panel/settings ownership.

A disabled browser in the factory response must remove both contributions, retain
unrelated Jobs and provider settings, reject later settings registration by the
same disabled owner, and tolerate repeated disable calls. Browser state is restored
between tests. This exercises loader removal and registration blacklisting, not
just consumers checking whether a manually removed panel exists.

## 2026-10-14 — `excluded` overrides `protected`

A factory row with `excluded: true` means the running distribution never loaded that builtin (cloud excludes
`builtin.browser`); it is not the user's "disabled" and `protected` ("a user cannot switch this off") says nothing about it.
`loadPlugins` therefore removes an excluded row's UI even when the row is protected (only `builtin.ui` is always skipped).
The test "a builtin the distribution excludes loses its UI even when it is protected" pins this; reverting it brings back
the cloud red banner. `excluded` is a required field of the row, so every fixture here states it.
