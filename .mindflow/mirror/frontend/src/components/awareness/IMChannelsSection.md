---
code_file: frontend/src/components/awareness/IMChannelsSection.tsx
stub: false
last_verified: 2026-05-22
---

## Why it exists

Single grouping component inside the Awareness panel for **all** IM
channel bindings (currently Lark + Slack; future Telegram). Replaces
having one top-level Lark card and one top-level Slack card with a
collapsible "IM Channels" section that scales to N channels without
visually overwhelming the panel.

Without this section the awareness panel grew a new top-level entry
every time we added a channel. Three-level disclosure keeps the
default view tight while letting the user drill into any one channel
on demand.

## Design decisions

- **Three-level disclosure.**
  1. **Collapsed**: ``▶ IM Channels  N/M connected  [Manage]`` — no
     network, no nested components rendered.
  2. **Expanded**: ``▼ IM Channels`` + a row per channel with a
     status badge (``✓ connected`` or "not bound").
  3. **One channel open**: that channel's full config panel
     (``LarkConfig`` or ``SlackConfig``) renders inline.
- **``IM_CHANNELS`` array is the single extension point.** Adding
  Telegram in Phase 4 = one entry: ``{key, label, Icon, Component,
  fetchConnected}``. No other change in this file. The ``ChannelEntry``
  interface enforces shape.
- **``fetchConnected`` is per-channel, not generic.** Each channel's
  ``data.enabled`` / ``data.is_active`` truthiness convention differs
  (Slack uses ``enabled``, Lark uses ``is_active``). Pushing the
  decision into the entry keeps the section component dumb.
- **Status fetched on mount AND on every section open.** Originally
  the fetch was lazy (only on open) but that left the Level-1
  collapsed badge stuck at ``0/3 connected`` until the user clicked
  to expand — bindings created via agent chat, in a prior session,
  or by another tab looked broken. As of 2026-05-22 a mount-time
  ``useEffect`` does the initial fetch so the count is correct on
  first paint. The toggle-time fetch is preserved as a cheap
  "user is actively looking at it again, refresh stale" path.
- **Render-time agent-change detection collapses inline panels.**
  Switching ``agentId`` while a channel was expanded would show stale
  config — we close the inline panel immediately. The pattern uses
  ``setState`` during render (allowed by react-hooks rules when
  comparing previous-render-stored value vs current prop) instead of
  a ``useEffect``, which would have a one-frame flash.
- **Heavy components are conditionally rendered, not just hidden.**
  ``LarkConfig`` / ``SlackConfig`` each fire their own credential
  fetch on mount; rendering them only when expanded means closed
  channels make zero API calls.

## Upstream / downstream

- **Upstream**: ``AwarenessPanel.tsx`` (or whichever parent groups
  the awareness widgets).
- **Downstream**:
  - ``LarkConfig`` / ``SlackConfig`` — the per-channel config UIs.
  - ``api.getLarkCredential`` / ``api.getSlackCredential`` — used
    only for the connected-status summary.
  - ``useConfigStore`` for the current ``agentId``.

## Gotchas

- ``connectedCount`` is computed from the cached ``connectedMap``,
  which is refreshed on: mount, agent change (``agentId`` dep in
  ``refreshConnected``), section open, and any child config's
  ``onBindStateChange`` callback (bind / unbind / test). The
  Level-1 count is now correct without user interaction.
- The mount-time ``useEffect`` calling ``refreshConnected`` is
  silently flagged by ``react-hooks/set-state-in-effect`` because
  the callback chain reaches ``setConnectedMap``. The rule prefers
  Suspense / React Query / SWR for server-state fetches; the rest
  of this codebase (LarkConfig, SlackConfig, TelegramConfig) uses
  raw ``useEffect`` for the same pattern. Adopting Suspense here
  alone would be inconsistent — disabled with a per-call comment
  and rationale in the source.
- ``refreshConnected`` swallows errors and falls back to ``false`` for
  that channel. Misleading if the API is down — the user sees "not
  bound" instead of "unknown / error". Watch for this in support.
- Adding a channel without an ``Icon`` from ``lucide-react`` will type-
  fail — the ``ComponentType<{ className?: string }>`` constraint is
  there to keep the row visually consistent.
