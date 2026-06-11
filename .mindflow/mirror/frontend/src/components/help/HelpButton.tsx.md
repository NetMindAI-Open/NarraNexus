---
code_file: frontend/src/components/help/HelpButton.tsx
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 — first-visit auto-open

New users get the guide automatically ~700ms after the page settles
(`help_guide_seen_v1` localStorage flag, written when the guide is
DISMISSED — got it / Esc / backdrop — not when shown, so a mid-guide
reload shows it again). Missing anchors degrade gracefully, so a
brand-new user with no agent simply sees the setup notes. Storage
unavailable → never auto-open.

## 2026-06-11 (PM)

Prop change: `annotations` → `pages` (multi-page overlay).



# HelpButton.tsx — Bottom-left ? entry point

Fixed circular button (Owner-specified position) + `?` keyboard
shortcut (suppressed while typing in input/textarea/contentEditable).
Owns the overlay open state. Mounted by MainLayout's ChatView with the
chat-view manifest.
