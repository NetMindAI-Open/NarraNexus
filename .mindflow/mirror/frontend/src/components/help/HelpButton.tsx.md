---
code_file: frontend/src/components/help/HelpButton.tsx
last_verified: 2026-06-20
stub: false
---

## 2026-06-20 — moved bottom-left → bottom-right

The `fixed bottom-4 left-4` position collided with the sidebar
footer's `ThemeToggle` (also bottom-left): the z-150 help button
covered the theme toggle, so it could be neither seen nor clicked.
Owner authorized relocating the help button to `bottom-4 right-4`,
which clears the left sidebar footer and sits to the right of the
right-rail's bottom "Refresh artifacts" control (no overlap).

## 2026-06-11 — first-visit auto-open

New users get the guide automatically ~700ms after the page settles
(`help_guide_seen_v1` localStorage flag, written when the guide is
DISMISSED — got it / Esc / backdrop — not when shown, so a mid-guide
reload shows it again). Missing anchors degrade gracefully, so a
brand-new user with no agent simply sees the setup notes. Storage
unavailable → never auto-open.

## 2026-06-11 (PM)

Prop change: `annotations` → `pages` (multi-page overlay).



# HelpButton.tsx — Bottom-right ? entry point

Fixed circular button (bottom-right; moved from the Owner's original
bottom-left on 2026-06-20 to stop covering the ThemeToggle) + `?` keyboard
shortcut (suppressed while typing in input/textarea/contentEditable).
Owns the overlay open state. Mounted by MainLayout's ChatView with the
chat-view manifest.
