---
code_file: frontend/src/components/awareness/SlackConfig.tsx
stub: false
last_verified: 2026-05-09
---

## Why it exists

Per-agent Slack workspace binding panel inside the dashboard's
Awareness section. Wraps the ``/api/slack/*`` endpoints in a
focused two-state UI so the owner can paste tokens and have the agent
talking to Slack within ~5 seconds.

## Design decisions

- **Exactly two visible states.**
  1. **Not bound** — show the bind form (Bot Token + App-Level Token
     inputs + a single "Bind Bot" button).
  2. **Bound** — show ``team_name (bot_user_id)`` + Test / Unbind.
  No intermediate "polling" state because — unlike Lark's OAuth
  device flow — Slack's tokens are validated synchronously by
  ``auth.test`` during the Bind request. The button either succeeds
  or surfaces an error in one round-trip.
- **Both inputs are ``type="password"`` with ``autoComplete="off"``.**
  Tokens are sensitive even before persisting; preventing autofill +
  hiding the typed characters covers the common screen-share scenario.
- **Bind / Test / Unbind all call ``api.*`` helpers — never
  ``fetch`` directly.** Single auth + base-URL handling stays in
  ``frontend/src/lib/api``.
- **Unbind goes through ``useConfirm``.** Destructive action — the
  message warns "agent will stop receiving Slack messages and lose all
  Slack tools". Less likely to happen by mistake.
- **``mountedRef`` guards setState on unmount.** Async fetches can
  resolve after the user navigates away (agent switch, tab change);
  writing state on a torn-down component is a noisy warning at best
  and a memory leak at worst.
- **Refresh button on header rather than auto-poll.** No polling means
  no wasted API hits; users hit Refresh / Test when they care. Mirrors
  the rest of the awareness panel's UX.
- **Effect resets local form state when ``agentId`` changes.** Token
  input drafts must NOT carry across agents — that's a security
  posture (paste meant for agent A doesn't accidentally bind to agent
  B if you switch tabs mid-paste).
- **Setup disclosure embeds the full Slack App Manifest.** Without it,
  users hit ``missing_scope`` failures because Slack's manual app
  configuration requires ~16 OAuth scopes + 5 event subscriptions +
  Socket Mode + bot user — easy to miss any one. The manifest
  pre-configures everything in a single paste. The YAML lives as a
  module-level constant ``SLACK_APP_MANIFEST_YAML`` at the top of the
  file.
- **Manifest YAML is duplicated with the backend.** Backend source of
  truth is ``src/xyz_agent_context/module/slack_module/slack_module.py``
  ``SLACK_APP_MANIFEST_YAML``. Hard-coding here avoids one API
  round-trip on every disclosure expand. When Slack adds a scope we
  need, ``grep "channels:history" src/ frontend/src`` finds both
  copies — the diff stays small. If divergence becomes painful (e.g.
  scope churn picks up), promote to a single ``GET /api/slack/manifest``
  endpoint.

## Upstream / downstream

- **Upstream**: ``IMChannelsSection.tsx`` mounts this inside the
  expanded Slack disclosure card.
- **Downstream**:
  - ``api.getSlackCredential`` / ``bindSlackBot`` /
    ``testSlackConnection`` / ``unbindSlackBot`` — the four
    REST-route wrappers.
  - ``useConfigStore`` for the active ``agentId``.
  - ``useConfirm`` hook for the unbind dialog.

## Gotchas

- The bind button stays disabled while either token is empty — but
  there is **no** client-side prefix validation (``xoxb-`` /
  ``xapp-``). Backend handles it. If you add a client check, mirror
  the backend's exact prefixes; otherwise users get conflicting
  error messages.
- ``credential.enabled`` is the soft-disable flag. The UI currently
  shows "Connected" any time ``credential`` is truthy; adding a
  "Disabled" badge would need a separate visual state.
- ``team_name`` falls back to ``team_id`` falls back to ``"Slack"`` —
  ``auth.test`` should always populate ``team_name`` but the chain
  guards against partial Slack responses.
