---
code_file: frontend/src/components/awareness/IMChannelsSection.tsx
stub: false
last_verified: 2026-05-08
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
- **Status fetch only on open.** ``handleToggleSection`` calls
  ``refreshConnected`` only when transitioning to ``open=true``.
  Two GETs (one per channel) — cheap, but no reason to pay them on
  every render.
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

- ``connectedCount`` is computed from the cached ``connectedMap`` set
  by the last refresh. Binding/unbinding inside a child config panel
  does NOT auto-refresh this count — the user has to click "Refresh
  status" or collapse + re-expand. Acceptable today; if it becomes
  jarring, expose a callback prop down to the child configs.
- ``refreshConnected`` swallows errors and falls back to ``false`` for
  that channel. Misleading if the API is down — the user sees "not
  bound" instead of "unknown / error". Watch for this in support.
- Adding a channel without an ``Icon`` from ``lucide-react`` will type-
  fail — the ``ComponentType<{ className?: string }>`` constraint is
  there to keep the row visually consistent.
