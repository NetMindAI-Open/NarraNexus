---
code_file: frontend/src/components/nm/bubble.tsx
last_verified: 2026-05-18
stub: false
---

# nm/bubble.tsx — MessageBubble (7 variants), BubbleGroup, BubbleMetaRow, TurnBreak

## Why it exists

Chat-specific surface primitives. Implements NM Axiom #4 (own bubble =
paper-raised), #5 (radius-lg = 14px), #6 (bracket-edge per species), and
the "sameGap 4 / turnGap 16" spacing rhythm from the NM spec.

- `MessageBubble` — 7 variants:
  - `human-other` / `ai-other`: other party's message; paper-warm + bracket-edge tl in species color
  - `own` / `own-lilac`: self message; own-paper bg + bracket-edge tr (ink default; overlap purple for co-write)
  - `system`: "Jane joined" centered ink-50 mono, no bubble
  - `tool-result`: inline tool output; sunken-paper + mono font
  - `error`: warm-oxblood + white text + error bracket-edge
- `BubbleGroup` — flex column container managing sameGap (4) / turnGap (16)
- `TurnBreak` — explicit spacer between bubble groups (height = turnGap - sameGap)
- `BubbleMetaRow` — sender (species-colored) + timestamp (ink-50 mono) row

## Upstream / Downstream

- **Upstream**: `BracketEdge` from `nm/bracket.tsx`. NM CSS vars.
- **Downstream**:
  - `chat/TurnTimeline` (M3) — wraps each turn's reply in MessageBubble
  - `chat/MessageBubble` (M3, existing component) — gets replaced internally to delegate to this NM bubble
  - `inbox/InboxRow` (M3) — preview snippet in human-other styling

## Design decisions

**7 variants, not 3.** Each variant carries semantics: own vs other,
species, role (system/tool/error). Folding to fewer variants forces callers
to repeat decoration logic. 7 named variants make call sites self-documenting:
`<MessageBubble variant="ai-other">` reads instantly.

**Own bubble has a *minimal* paper-lift shadow** (`0 1px 0 / 0 2px 4px`).
This is allowed exception per Axiom #4 — own bubble needs to feel "lifted
off the paper" rather than just "another patch of paper". Tested visually
in NM design稿 — the v5 "paper-raised" choice that survived 5 iterations.

**Tool-result uses same paper-warm as other-message** but adds mono font +
text-xs. The visual is "this is a quoted technical output, not a
conversational message" — sunken would say "this is depressed UI chrome",
which is wrong (a tool result IS the conversation, just typographically
different).

**System variant has no bubble wrapper** — center-aligned ink-50 mono
uppercase. Visually says "this is metadata", not "someone said this".
TurnTimeline (M3) will use this for joined/left/title-change events.

**BubbleGroup uses CSS `gap`, not margins on children.** Avoids margin
collapsing edge cases; clean for flex containers.

**TurnBreak vs gap.** A `<TurnBreak />` between two BubbleGroups produces
total spacing of `sameGap + (turnGap - sameGap) = turnGap`. Callers can
choose: one group with all messages (uses sameGap throughout) + manual
TurnBreaks between turns, or multiple groups (each manages its own
sameGap).

**Error variant is the only one with non-paper bg.** Critical errors need
to break the paper feel — a tool/LLM failure is a real "stop and notice"
moment. Warm-oxblood ensures Axiom #2 status palette compliance (NOT
Carbon orange).

## Gotchas

- `aria-label` on `<MessageBubble role="note">` should include sender + time
  for screen readers (e.g., "Jane Chen at 12:04"). MessageBubble doesn't
  auto-generate this; caller must pass it.
- `max-w-[80%]` constrains bubble width to 80% of parent flex track. For
  desktop centered chat (max-width: 760px), bubbles cap around 600px which
  matches NM design稿 phone-sized bubbles scaled to desktop.
- BubbleMetaRow species color = `--color-carbon/silicon/overlap` — these
  resolve to the lifted tints in dark mode automatically (light: #E8704A
  → dark: #FF7A5C).

## Related

- `nm/bracket.tsx` BracketEdge — every non-system bubble uses one
- `nm/identity.tsx` RingAvatar — typically renders to the left of the
  BubbleMetaRow + bubble stack
- `chat/MessageBubble.tsx` (existing) — restyled in M3 to delegate
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.4 + §6.1
