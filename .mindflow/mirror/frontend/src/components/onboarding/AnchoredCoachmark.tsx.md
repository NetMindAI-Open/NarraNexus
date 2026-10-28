---
code_file: frontend/src/components/onboarding/AnchoredCoachmark.tsx
last_verified: 2026-08-19
stub: false
---

# AnchoredCoachmark.tsx — the one shared coach-mark bubble

## Why it exists

GuideAgentCoachmark started as a character-for-character copy of
MigrationCoachmark (same anchor selector, same 500ms mount-race interval,
same fixed positioning, same arrow) — and both can be armed at once on a
local first run (new user + importable agents), which rendered two bubbles
pixel-overlapped at identical coordinates: dismiss one, an identical-looking
one is underneath. This extraction leaves ONE bubble implementation; callers
own only their gate and copy.

## Upstream / Downstream

**Callers:** `MigrationCoachmark` (gate lives in MigrationGuide, local mode,
post-modal) and `GuideAgentCoachmark` (gate = lib/guideCoachmark localStorage
state). Props: `anchorSelector` / `onDismiss` / `children` (translated body) /
`dismissLabel` — callers keep their own `t()` calls (zh-localization.test.ts
asserts the literal `t('onboarding.guideCoachmark.text')` in the guide file).

## Design decisions

- **One bubble per anchor at a time** (module-level claim map): the second
  instance stays dormant — a Map lookup per 500ms tick, NO querySelector —
  and takes over when the holder dismisses/unmounts. The 10s give-up counter
  only runs while owning the anchor, so queueing doesn't eat the mount-race
  budget; a blocked waiter's interval stays alive for the session, which is
  the accepted cost of the takeover behavior.
- Measuring/portal mechanics are verbatim from MigrationCoachmark (anchor
  rect via querySelector, skip-render on unchanged geometry, resize listener,
  interval stops once anchored or after ~10s, nothing on collapsed rail).

## Gotchas

- The claim map keys on the SELECTOR string — two coachmarks pointing at
  different anchors never queue on each other (and two selectors that
  RESOLVE to the same element but are spelled differently will NOT exclude
  each other — new coachmarks must reuse the existing selector literal).
- `anchorHolders` is module-level state: tests import the component fresh
  per case (vi.resetModules) so a leaked claim can't decide the next test.

## Tests

`__tests__/AnchoredCoachmark.test.tsx` pins the invariant three ways: same
anchor → ONE bubble (deleting the claim map goes red — verified by
experiment), holder unmount → the queued bubble takes over on the next tick
(deleting the cleanup release goes red as "second never appears"), different
anchors → both render. jsdom's zero-size getBoundingClientRect is stubbed
because the component deliberately ignores zero-size anchors.
