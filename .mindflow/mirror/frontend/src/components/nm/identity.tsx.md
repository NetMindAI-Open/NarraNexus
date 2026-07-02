---
code_file: frontend/src/components/nm/identity.tsx
last_verified: 2026-06-20
stub: false
---

## 2026-06-20 — added BindingDot ("carbon meets silicon" motif)

New export `BindingDot`: a carbon dot · hairline · silicon dot triad, read
left-to-right as "human binds to AI". It is the brand motif at the head of the
conversation panel (replacing the lone StatusDot in [[ChatPanel]]) and the
eyebrow of the [[OnboardingJourney]] empty state. Optional `pulse` breathes the
silicon dot so the motif doubles as a live-streaming cue without a second
indicator. Pure presentational, tokens-only (no SVG).

# nm/identity.tsx — Identity primitives (Ring/Group avatars, SpeciesDot, AvatarStack)

## Why it exists

Implements NM Axiom #3 (ring over fill) and Axiom #1 (species color = identity)
for "who is this entity". Five exports cover every identity rendering in the
app:

- `RingAvatar` — single entity (human=Carbon ring, AI=Silicon ring,
  overlap=purple, neutral=ink-50). 5 sizes (xs/sm/md/lg/xl). Renders a
  single SVG-free ring + center 1-2 char label or an image inside the ring.
- `GroupAvatar` — multi-member group encoded as arc segments of a single
  ring (sliced by `stroke-dasharray`). Center shows total count. Lets users
  "see the species ratio at a glance without opening the popover".
- `SpeciesDot` — small dot for marking single rows / inline annotation.
  Default is a hollow ring; `filled` variant for status pills.
- `AvatarStack` — overlapping RingAvatars with `+N` overflow chip.
- `AvatarWithStatus` — wraps any avatar with a bottom-right status dot
  (online/offline/etc.). Status dot color comes from status palette
  (Axiom #2), NOT species color.

## Upstream / Downstream

- **Upstream**: `@/lib/utils.cn` for className merging. NM tokens via CSS vars.
- **Downstream**:
  - `chat/MessageBubble` (M3) — every message has a RingAvatar
  - `inbox/InboxRow` (M3) — GroupAvatar for multi-party conversations
  - `chat/ChatPanel` group header (M3) — GroupAvatar in title bar
  - `pages/AgentProfilePage` (M3) — large RingAvatar xl
  - `pages/ConnectionsPage` (M3) — RingAvatar per row
  - `awareness/AwarenessFeed` (M3) — SpeciesDot for event source identity

## Design decisions

**Species color comes from CSS var, not prop.** `--color-carbon` /
`--color-silicon` resolve to the right tint per theme automatically
(light: `#E8704A` / dark: `#FF7A5C`). Avoids per-component theme checks.

**Group avatar uses a single SVG circle + dasharray, not many small circles.**
Trade-off: arc gap between segments (1px) is calculated rather than visually
exact. Benefit: scales smoothly at any size; renders in one paint; matches
NM design稿's "single ring divided into species arcs" visual literally.

**SpeciesDot ring vs filled.** Default ring matches Axiom #3. `filled=true`
escape hatch for status-pill use cases where ring + center fill is needed
(e.g., "online" filled green dot).

**`label.slice(0, 2).toUpperCase()` in RingAvatar.** Most names produce a
single-char initial; some compound names ("AI") want 2 chars. Slice handles
both without forcing callers to pre-process.

## Gotchas

- `species="neutral"` resolves to `var(--nm-ink50)` — not a "species" per
  Axiom #1, but a frequent need for "unidentified" or generic UI use cases
  (e.g., AvatarStack overflow chip).
- GroupAvatar with one member produces a complete ring + a (calculated)
  full-circumference segment. Works correctly but verify visually if used
  for single-member edge case.
- AvatarWithStatus assumes child is a circular avatar; status dot is
  absolute-positioned at bottom-right with 2px paper border for separation.
  If wrapped around a non-circular child, the offset will look wrong.

## Related

- `nm/bracket.tsx` — BracketCornerMarks complements RingAvatar for selection
- Spec: `reference/self_notebook/specs/2026-05-18-nm-design-system-design.md` §5.1
