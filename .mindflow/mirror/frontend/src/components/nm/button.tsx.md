---
code_file: frontend/src/components/nm/button.tsx
last_verified: 2026-05-18
stub: false
---

# nm/button.tsx — Button primitives (Button, IconButton, ButtonGroup, SplitButton)

## Why it exists

4 button primitives implementing NM Axioms #3 (primary = ink-fill exception),
#1 (danger = warm oxblood, NEVER Carbon), #5 (radius-sm = 6px for buttons),
and #8 (motion-fast = 150ms transition).

- `Button` — 5 variants × 3 sizes; primary/secondary/ghost/danger/link;
  optional leading/trailing icons; loading state with inline spinner
- `IconButton` — circular icon-only button; ring or plain; requires `label`
  for screen reader access
- `ButtonGroup` — horizontal/vertical grouping that shares hairlines (uses
  Tailwind v4 child selectors `[&>...]` to reach into children's rounded
  corners)
- `SplitButton` — primary action + dropdown arrow; consumer wires up the
  popover/dropdown via `onDropdownClick`

## Upstream / Downstream

- **Upstream**: `@/lib/utils.cn`, NM CSS vars
- **Downstream**:
  - Every CTA in the app (Login, Settings, ConfirmDialog, etc.) — M3-M5
  - IconButton used by Toast close, Composer attach/send, Toolbar actions
  - SplitButton used by Deploy / Export actions where dropdown reveals
    advanced options

## Design decisions

**Danger = `--color-error` (warm oxblood `#C95A4D`).** Critical:
DO NOT use Carbon orange for danger. Carbon = "human" in NM (Axiom #1);
using it for "delete" creates a permanent association of "human action" with
"destructive risk", which is exactly the wrong message for a human-AI
collaboration product.

**Primary = ink fill.** Axiom #3 explicit exception: filled is OK for the
single primary action (most important call to action). Ghost is the default
for "secondary actions in toolbars". Secondary (paper-raised + ink border)
is for "alternative important actions".

**Link variant strips size class.** A "link" button reads as inline text and
should sit at the baseline of surrounding prose — height/padding constraints
would break that.

**Loading state replaces leading slot with spinner, keeps label.** Common
UX: "Saving..." button stays clickable-looking but disabled. Implementing
this in the primitive keeps callers from reinventing.

**IconButton requires `label` prop (TypeScript-enforced).** Screen-reader
accessibility — icon-only buttons without aria-label are unusable for
keyboard/SR users. Required string forces the call site to think about it.

**ButtonGroup uses Tailwind v4 child selectors `[&>...]`** to override
children's rounded corners + borders. This avoids forcing callers to wrap
buttons in different variants. Tailwind v4 supports this natively in
className.

**SplitButton style sync.** When variant=primary, the dropdown IconButton
inherits the primary fill manually via a className override — IconButton's
default is ring-on-paper, so without override the dropdown would visually
clash with the primary primary button. A future refactor could add
`appearance="primary"` to IconButton; not done now per YAGNI.

## Gotchas

- `Button type` defaults to `"button"` (overriding browser default
  `"submit"`). Prevents accidental form submission. Set `type="submit"`
  explicitly inside forms.
- ButtonGroup's child selectors target `[data-nm=button]` — only NM Buttons
  participate in the group styling. Mixing in non-NM buttons leaves their
  corners + borders intact (which is usually wrong).
- SplitButton's dropdown is not implemented internally — caller must wire
  up a Popover/Menu via `onDropdownClick`. M2 Popover wrapper will provide
  a convenience integration.

## Related

- `nm/feedback.tsx` Spinner — standalone version of the inline spinner
- `nm/feedback-radix.tsx` Popover — typical SplitButton dropdown shell
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.8
