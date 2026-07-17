---
code_file: frontend/src/components/awareness/LarkConfig.tsx
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — activation toggle + parent-list sync

Renders the shared `ChannelActiveToggle` in the bound state (flip `is_active` via `POST /api/lark/set-active`) — primary use is activating a bundle-imported (inactive) credential. The toggle handler AND the header refresh button now call `onBindStateChange` so the parent `IMChannelsSection` status badge updates immediately (previously stale until remount).

# LarkConfig.tsx — Per-agent Lark/Feishu bot binding UI

## Why it exists

The Settings → Awareness panel slot for binding a Feishu/Lark app to the
current agent. Drives the full lifecycle: collect App ID/Secret/owner email,
POST to `/api/lark/bind`, then track auth_status (bot_ready → user_logged_in
via OAuth device flow) and offer unbind. Co-owns the state machine with
backend `_lark_credential_manager` (which is the source of truth for
auth_status values).

## Upstream / Downstream

- **Used by**: `IMChannelsSection` (sibling for Slack / Telegram configs).
- **Calls**: `api.bindLarkBot / unbindLarkBot / larkAuthLogin /
  larkAuthComplete / getLarkCredential`.

## States rendered

1. No credential → bind form (App ID + Secret + Owner email + Feishu/Lark)
2. `bot_ready` → bot working, OAuth pending (login button + unbind)
3. `user_logged_in` → fully connected (status + unbind)
4. `expired` / `not_logged_in` → broken, force re-bind

## 2026-05-27 — structured error rendering (translator card)

The bind form's error display used to render the raw `error` string in a
red div, so users saw `"99991672 App scope not enabled"` and had no idea
what to do. The bind response now optionally carries `error_detail`
(populated by `_lark_error_translator` on the backend) with `{title,
message, action_hint, console_url, raw_message}`. When present, we render:

- Title in bold (e.g. "Required permission scope is not enabled")
- 1-2 sentence explanation
- "What to do: …" actionable hint
- Clickable `Open the relevant developer console page →` link from
  `console_url`
- Collapsible "Technical details" showing `[code] raw_message` for
  diagnostics

Falls back to the plain `error` string when `error_detail` is absent
(unknown errors / older backends).

## Gotchas

- The `polling` loop for OAuth completion uses captured `targetAgentId`
  to avoid the classic stale-closure bug when the user switches agents
  mid-polling.
- `mountedRef` guards every async setState — the parent (`IMChannelsSection`)
  can unmount this mid-flight when the user closes the panel.
