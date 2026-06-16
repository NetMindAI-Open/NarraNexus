---
code_file: frontend/src/components/nm/form.tsx
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 — FormField label↔input a11y association fix

**What changed**: The `useId()`-generated id that wires `<label htmlFor>`
to its control was previously applied to the `data-form-field-control`
wrapper `<div>`. A `<div>` is not a focusable element, so
`getByLabelText` (Testing Library) and screen readers both failed to
resolve the label → input relationship.

**Fix**: `FormField` now clones the **first valid child element** and
injects the `useId()` value as the `id` prop directly onto that child
(typically an `<input>`, `<select>`, or `<button>`). The wrapper div no
longer carries the id at all.

**Impact**: 5 call-sites across the app confirmed non-regressing (nm
suite 146/146 green). Any call-site that relied on selecting the wrapper
div by id would break — none did.

---

# nm/form.tsx — Form primitives (9 components)

## Why it exists

All form controls in one file so the visual language stays consistent
across `<input>`, `<select>`, custom toggles, etc. Implements:
- Axiom #4 (inputs sit in SunkenWell — paper-warm + inset hairline)
- Axiom #3 (Toggle = bracket-wrapped pill, Radio = ring, Checkbox = real bracket with check)
- Axiom #7 (FormField labels = mono uppercase ink-50)

Components:

- `FormField`: label + control + hint + error wrapper with proper htmlFor
  wiring (auto-generated id via `useId()`)
- `TextInput`: input wrapper with focus underline + leading/trailing icon slots
- `Textarea`: multi-line variant; uses `.nx-textarea` class for macOS scrollbar fix
- `Select`: native `<select>` styled to match input
- `Toggle`: switch role + 44×24 pill; ink-fill when on
- `Checkbox`: 18px square + check icon when checked
- `Radio`: 18px circle + 8px dot when selected
- `Slider`: native range input + label/value display; accent-color = ink
- `SearchInput`: TextInput + bracket-shaped search icon + clear button + Escape clears

## Upstream / Downstream

- **Upstream**: NM CSS vars, `useId` for label/control wiring
- **Downstream**: every form in the app. SettingsPage (M4) is the heaviest
  user; Setup wizard (M4); BundleImport (M4); FilterBar in chat (M3).

## Design decisions

**Focus underline via inline style + onFocus/onBlur handlers.** Because
Tailwind v4 doesn't have a built-in `focus-within:inset-bottom-stroke`
utility, we drive the box-shadow imperatively. Trade-off: imperative state
on a CSS variable would be cleaner, but the inline approach is one less
new utility class and works in all browsers without :focus-within quirks.

**Toggle, Checkbox, Radio use `<button role=…>` instead of native
`<input type=checkbox|radio>`.** Reasons:
1. Custom visual without `appearance: none` hacks
2. Easier to wire `aria-checked` semantics
3. Native checkbox doesn't accept rich children
Caller can wrap in `FormField` for full a11y wiring.

**Slider uses native `<input type=range>`** with `accent-color`. Native
range is plenty flexible for NM (thin ink rail + handle), and avoids the
complexity of building a custom slider with pointerdown/move/up handling.

**SearchInput Escape-to-clear.** Standard pattern; better than requiring
users to find the x button.

**Toggle handle uses paper color on ink track when ON.** Reads as
"paper sliding over ink groove" — matches the paper-physical metaphor.

**Checkbox is filled ink when checked, ring outline when unchecked.**
Different from Radio which stays ring + adds center dot. The distinction
reinforces "checkbox = binary commitment / radio = mutually exclusive
choice".

## Gotchas

- `Select` uses `appearance: none` on the inner `<select>` so the OS
  arrow doesn't appear alongside our SVG chevron. The custom SVG is
  pointer-events-none so click-through still works to open the native
  dropdown.
- `Toggle` requires controlled `checked` + `onChange`. There's no
  uncontrolled mode — keeps the implementation simple. Local state for
  uncontrolled patterns is the caller's responsibility (e.g., useState).
- `Slider` value display uses the prop `value` directly (no
  intermediate state) — fires `onChange` on every range change. For
  expensive listeners (e.g., recalculating a chart), debounce in caller.
- `TextInput`'s leading/trailing icons must NOT include their own
  pointer-events block — they're inside the input wrapper, so clicks
  on them won't focus the input. Use cursor:pointer + onClick on icon
  to forward focus if needed.

## Related

- `nm/surface.tsx` SunkenWell — visually matches TextInput/Textarea well
- `nm/button.tsx` Button — used inside SearchInput trailing slot
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.6
