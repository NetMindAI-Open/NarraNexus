---
code_file: frontend/src/components/bookmarks/__tests__/drawerPinToggle.test.tsx
last_verified: 2026-07-30
stub: false
---

# drawerPinToggle.test.tsx — pinning must not rebuild the panel

## 为什么存在

Guards the invariant behind [[BookmarkDrawer]]'s 2026-07-30 (2) fix: toggling
the pin changes only how the panel is positioned, never where it lives in the
DOM. Violate that and React remounts the panel, silently resetting everything
the user set up inside it.

This test earned its place: it **caught the first fix attempt as wrong**.
Collapsing the caller's two `<BookmarkDrawer>` elements into one was necessary
but not sufficient, because the component still wrapped one mode in
`createPortal` and not the other — also a tree-shape change, also a remount.
Without the mount counter that would have shipped looking correct.

## 上下游关系

- **测的是**: [[BookmarkDrawer]] in isolation, driven by a local `Host`
  component that mirrors how [[MainLayout]] owns `pinned` as state.
- The stand-in `StatefulPanel` does two jobs: counts mounts (via a `useState`
  initialiser, which runs once per mount) and holds a filter value standing in
  for the real panels' local UI state.

## 新人易踩的坑

`toHaveBeenCalledTimes(1)` on the mount counter is the whole point — asserting
that the *content* still renders after toggling would pass even with a remount,
because a fresh panel renders fine. It just forgets everything.

The width assertion reaches the column via
`closest('div[style*="width"]')` rather than a test id, so it keeps working if
the frame's classNames are restyled but breaks loudly if the inline width
disappears (e.g. someone moves the width back out to a caller-side wrapper —
the exact regression that caused the remount).
