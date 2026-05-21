---
code_file: frontend/src/components/nm/status.tsx
last_verified: 2026-05-18
stub: false
---

# nm/status.tsx — StatusDot, StatusBadge, ConnectionBanner, Toast

## Why it exists

Status primitives implementing Axiom #2 (warm-tinted status palette, NOT
species colors). All status reads (success/warn/error/info/neutral) flow
through these 4 components — anywhere in the app that needs "this is OK /
this is failing" feedback.

- `StatusDot` — small ring (4 sizes) with filled / ring / pulse variants
- `StatusBadge` — hairline pill + dot + label (status-colored or ink)
- `ConnectionBanner` — 4-state non-blocking banner (synced hidden,
  connecting, sync-error+Retry, offline); NM 8th evolution rule
- `Toast` — paper-raised with left status color bar; title + description +
  action + dismiss

## Upstream / Downstream

- **Upstream**: NM CSS vars (`--color-success`, etc.). Status colors auto-
  remap to lifted siblings in dark mode (Axiom #2).
- **Downstream**:
  - `chat/ChatPanel` (M3) — ConnectionBanner at top
  - `AgentCompletionToast` (M3) — replaced by Toast
  - `system/SystemPage` (M4) — StatusBadge per health row
  - `awareness/AwarenessFeed` (M3) — StatusDot per event source
  - Any error/success feedback in forms (M2 form primitives)

## Design decisions

**ConnectionBanner returns null for `synced` state.** Per NM 8th evolution
rule ("synced state自己消失 — 让网络状态在该说话时说话，不藏"). When
network is OK, the banner literally doesn't render — frees vertical space
for the conversation.

**`role="status"` + `aria-live="polite"` on both Banner and Toast.**
Screen readers announce changes without interrupting; matches WCAG live
region best practice.

**Toast uses raised + soft shadow combo** (`0 4px 12px rgba(42,38,32,0.08)`).
This is the SECOND allowed shadow exception (after RaisedPanel + own bubble):
Toast needs to feel "appearing over content", and pure paper-raised isn't
enough when overlaid on a colored chart or image. Light shadow at 8% alpha
preserves paper feel.

**StatusBadge dot is always filled** (not ring). The badge is already
hairline-bordered; a ring-dot inside a ring-pill is too much hairline.

**ConnectionBanner left border = 4px status color bar.** Carries the
"strip of status" identity at a glance — readable in peripheral vision.

## Gotchas

- Toast's `role="status"` means dismissing it programmatically doesn't fire
  any cancellation event for ARIA — that's fine for transient notifications,
  but caller-driven dismissal should still call `onDismiss` callback.
- ConnectionBanner state="connecting" uses `pulse` animation on the dot.
  The pulse is paused under `prefers-reduced-motion` automatically by the
  global index.css rule.
- StatusBadge inkLabel=true uses ink color for label but KEEPS status color
  for border + dot. Useful when status is informational not alarming
  ("Online" vs "Failed").

## Related

- `nm/feedback.tsx` Spinner — sibling motion primitive
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.5
