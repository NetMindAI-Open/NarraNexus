---
code_file: frontend/src/components/agents/TeamMemberAvatars.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — ⚠️ CONFLICTS WITH THE 2026-08-27 OWNER RULING BELOW (B6 — flagged for Owner review, not silently overridden)

The local `formatFramework` this file's own "Gotchas" section documents as a DELIBERATE Owner
ruling ("keep them independent rather than share... this component follows that same precedent
instead of introducing a shared util the Owner has already rejected once") was removed. This
component now imports `lib/frameworkBrand.ts`'s `formatFrameworkFromList`, called with no live
`frameworks` list (so it lands on the exact same static-then-title-case fallback chain
`formatFramework` always used — except the two labels that were WRONG, "Codex" and "Nexus Power",
now read "Codex CLI" and "NexusPower-beta", matching the canonical picker labels everywhere
else). This was done on an explicit, specific instruction from this session's dispatching
coordinator ("Replace... formatFramework in TeamMemberAvatars.tsx with... a single shared
helper"), received AFTER this file's own local copy already existed — I did not re-derive this
independently. I am flagging the direct conflict with the recorded 2026-08-27 ruling here rather
than either silently complying or silently reverting: the old ruling predates any live
backend-provided `display_name` (arguably a materially different concern — sharing the SOURCE OF
TRUTH for a field the backend now computes, vs. sharing a purely-local static lookup), but
whether that distinction is enough to supersede the ruling is an Owner call, not mine.

# TeamMemberAvatars.tsx — Agent-list-style member avatars for a Team row

Mirror image of [[AgentTeamAvatars.tsx]]: that component shows which Teams an
Agent belongs to (on an Agent row); this one shows which Agents belong to a
Team (on a Team row), one Tooltip-triggered avatar per member instead of a
plain member-count string. Both live side by side in `components/agents/`
because they're the same "avatar + hover Profile" idiom applied in opposite
directions — [[../../pages/DashboardPage.tsx]]'s Teams tab is the only current
caller.

## How it works

Renders `memberAgentIds` as overlapping `RingAvatar`s (`-space-x-2`), capped at
`max` (caller passes `3` from the Teams tab: the Members column is 132px and a
32px `sm` avatar overlapped by `-space-x-2` costs 24px each, so 3 avatars plus
the `+N` bubble is ~110px — 4 would overflow) with a `+N`
overflow bubble past that; the overflow bubble is a dead-end Tooltip (member
count only), not a link — no roster-browsing surface exists yet to send it to.

Each visible avatar is a Tooltip trigger AND a navigate button in one: hover/
focus opens a mini profile card (name, Lock/Globe for `is_public`, a status
dot + label via a locally-computed status cell, description, then Runtime/
Model/Owner rows), and clicking calls
`navigate('/app/agents/:id', { state: { from: 'dashboard' } })` — the same
destination and breadcrumb-origin tag the Agents table's **identity block**
(avatar + name — not the whole row, which still toggles the inline detail)
already uses, so the Profile page's back button behaves identically regardless
of which table sent the user there.

Member data comes from two maps the caller already had lying around
(`agentsById` from `rosterAgents`, `statusById` from the live status feed) —
no new requests. `agentsById` only contains agents the viewer owns/rosters, so
a team member owned by someone else (shared/public agent in the team) resolves
to `undefined`: the avatar still renders (id-derived initials, id shown as the
tooltip name), but Runtime/Model fall back to `—` and Owner shows the raw
`created_by` id. This is the same gap the Leader column has always had for
non-rostered leaders — not something this component introduces or attempts to
fix.

## 2026-08-25 — Overflow badge is plain text, not a `RingAvatar`

The `+N` overflow indicator past `max` was originally a `RingAvatar
species="neutral"` with `+N` as its label — Owner ruling: no ring here, it
should read as plain small text, not another avatar. It's now a bare
`+N` span (still the same Tooltip trigger, still shows the total member
count on hover); only the visible members get the ring-avatar treatment.

## Gotchas

- **The status-cell mapping is a local copy**, not an import from
  [[../../pages/DashboardPage.tsx]] or [[../../pages/AgentProfilePage.tsx]].
  (2026-09-07: the framework LABEL is no longer a local copy — see the
  flagged entry above — but the status-cell logic still is, unaffected by
  that change.)
- **No hostname/runtime-host field exists anywhere in `AgentInfo`** — the
  Runtime row shows only the formatted framework label (e.g. "Claude Code"),
  never a machine name. Don't invent one without a backend field to back it.
- **Empty Model shows `—`**, matching the rest of the app — no bespoke
  "runtime default" string was introduced for the empty case.
- The wrapping `<span>` stops click propagation (same as `AgentTeamAvatars`)
  so a stray click inside the avatar cluster can't misfire a future row-level
  action; today the Teams tab row itself has no click handler, so this is
  purely defensive.
