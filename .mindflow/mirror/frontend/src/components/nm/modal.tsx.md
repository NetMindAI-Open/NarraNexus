---
code_file: frontend/src/components/nm/modal.tsx
last_verified: 2026-05-18
stub: false
---

# nm/modal.tsx — Dialog, ConfirmDialog, Drawer, Sheet

## Why it exists

4 overlay primitives. Implements Axiom #4 (warm-ink backdrop + paper-raised
card + soft lift shadow exception) + Axiom #6 (Dialog can wear
BracketCornerMarks for focus emphasis).

- `Dialog` — center modal with bracket corner marks (default ON for
  "focused container"); 3 sizes; optional title/close/footer
- `ConfirmDialog` — yes/no flow built on Dialog; destructive uses
  Button variant=danger (warm oxblood, NOT Carbon)
- `Drawer` — right (default) or left slide-in panel; 3 widths
- `Sheet` — mobile bottom-sheet with drag handle

## Upstream / Downstream

- **Upstream**: `nm/bracket.tsx` BracketCornerMarks (Dialog only), `nm/button.tsx` Button (ConfirmDialog)
- **Downstream**:
  - Every confirmation flow (delete agent, reset memories, etc.) — ConfirmDialog
  - Settings sub-flows that need focus — Dialog
  - ChatPanel context panel on narrow screens — Drawer
  - Mobile filter UI — Sheet
  - Future agent profile quick-view — Drawer or Sheet by viewport

## Design decisions

**Backdrop is `rgba(42,38,32,0.45)` warm semi-transparent ink, not pure
black.** Per Axiom #4 — even modals stay in the paper world. The faint
`backdrop-filter: blur(2px)` adds depth without breaking the warmth.

**Dialog soft shadow is the THIRD shadow exception** (after RaisedPanel
and Toast): `0 12px 36px rgba(42,38,32,0.16)`. The larger blur is required
because the modal floats over an unknown surface (could be a bright chart
behind backdrop); 16% alpha keeps it warm-tinted.

**Dialog wears BracketCornerMarks by default.** Per Axiom #6: the most
"this is a focused container, look at me" UI moment in the app. Can be
disabled via `cornerMarks={false}` for less-emphatic dialogs.

**ConfirmDialog destructive = Button danger (warm oxblood).** Critical:
NOT Carbon orange. Carbon = human; destructive can't be associated with
human identity.

**Drawer width presets sm/md/lg.** 320/420/560 covers most NarraNexus
needs. Below 320 the touch target gets too cramped; above 560 it's not a
drawer anymore (use Dialog lg).

**Sheet has a draggable-looking handle but isn't actually draggable.**
Visual affordance only — the slide-down via Escape or backdrop tap is
sufficient interaction. Real drag-to-dismiss would need pointer event
plumbing; YAGNI for M1.

**All 4 share** body-scroll lock + Escape-close hooks. DRY.

## Gotchas

- `useScrollLock` modifies `document.body.style.overflow` — if multiple
  modals open simultaneously, the inner one's cleanup will restore the
  outer one's lock (correct behavior). If body had a custom overflow
  before the modal opened, it's saved and restored.
- `useEscapeClose` attaches a window-level keydown listener — multiple
  open modals will all close on Escape (top-most stays; rest depends on
  React mounting order). For nested modals, the outer one should be
  rendered later in the tree so it closes first.
- Dialog z-index: backdrop 1000, content 1001. Matches the spec §6.11
  z-index hierarchy. ConfirmDialog and Drawer use the same.
- `Backdrop onClick` triggers `onClose`. If a user wants to click inside
  the modal without closing, click handlers inside the dialog must call
  `e.stopPropagation()` — but normal child clicks DON'T propagate to the
  backdrop because they're in a separate root-level div. No issue.

## Related

- `nm/button.tsx` Button — used in ConfirmDialog footer
- `nm/bracket.tsx` BracketCornerMarks — wraps Dialog content
