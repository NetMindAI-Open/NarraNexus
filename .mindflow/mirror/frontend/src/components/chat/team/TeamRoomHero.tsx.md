---
code_file: frontend/src/components/chat/team/TeamRoomHero.tsx
last_verified: 2026-07-30
stub: false
---

# team/TeamRoomHero.tsx — the empty room's opening screen + the rule cards it lends out

## Why it exists

A team room's addressing rules (un-addressed → the default responder, `@name` →
one member, `@all` → every member runs a turn) are real and non-obvious, but the
surface that stated them — `TeamRoomGuide` (deleted 2026-07-30), a grey 11px banner pinned above the
transcript — was **permanent tip debt**: a strip of the room spent forever on a
fact the user needs twice, plus a fold state persisted in localStorage to make
the debt bearable. Owner's verdict was that it looked cheap.

This file states the same rules in the two moments they are wanted instead of
always. `TeamRoomHero` owns the empty room — the one moment there is nothing
else to look at — and `GuideRuleCards` is the same rule block on its own so
[[TeamChatPanel]]'s member-bar `?` popover can raise it after the transcript
has taken the space. Two exports rather than two files because the hero IS the
cards plus an identity block; splitting them would put one visual unit in two
places.

## This file does not do

Persist anything. The banner's `nx.team.guide.<teamId>` fold memory died with
it — a hero only shows on an empty room and a popover is dismissed by the next
click, so there is no state worth remembering, and no `localStorage` failure
mode to guard.

## Upstream / downstream

- **Used by**: [[TeamChatPanel]] only. `TeamRoomHero` replaces the `Users2 +
  chat.team.empty` block on the `messages.length === 0` branch; `GuideRuleCards`
  is rendered a second time inside the member bar's help popover, so the room
  never states its rules two different ways.
- **Depends on**: `RingAvatar` from `@/components/nm` for the member stack, and
  the existing `chat.team.guide.*` i18n group — the rule bodies (`plain`,
  `plainWithLead`, `mention`, `broadcast`, `relay`) are inherited from the
  banner; only the three `*Title` keys are new.

## Design decisions

- **Names the default responder** (`plainWithLead` with `leadName`) rather than
  saying "the team lead". Who answers an un-addressed message is the one fact a
  freshly opened room cannot show you any other way; the member bar marks the
  same agent with a dot.
- **`accent` is a prop, not read from the team**, because the cards render in
  two parents and only the panel knows the team's colour. It is injected via
  `color-mix(in srgb, ${accent} 12%, transparent)` on the icon tile — a team
  colour reaching the tip surface is what makes it feel like this room's screen
  rather than generic chrome.
- **Avatars cap at `MAX_AVATARS` (5) + a "+N" chip.** A 20-agent team would
  otherwise turn the hero into an avatar wall and push the rules off screen.
- **Zero members swaps the avatar row for `chat.team.noAgents` but keeps the
  cards.** The rules are what the room is FOR; a team you have not staffed yet
  is exactly when you are still learning how it works.

## Gotcha / edge cases

- **Trigger**: two members whose display names are identical → **Symptom**:
  React duplicate-key warning in the avatar stack → **Root cause**: the stack is
  keyed by name, because the hero receives `memberNames: string[]` and never
  sees `agent_id`. Names are the panel's `m.name || m.agent_id`, so collisions
  need two same-named agents in one team.
- **Trigger**: reading the avatar count off `memberNames.length` in a test →
  **Symptom**: the assertion is off by one for teams over five → **Root cause**:
  the "+N" chip is a plain `<span>`, not a `RingAvatar`; only the first five
  carry `data-nm="ring-avatar"`.

## Related constraints

- 铁律 #23 (package once a surface reaches three files) — this file joins
  `chat/team/` rather than sitting flat in `components/chat/`.
