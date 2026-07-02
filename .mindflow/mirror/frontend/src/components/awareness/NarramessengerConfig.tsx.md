---
code_file: frontend/src/components/awareness/NarramessengerConfig.tsx
stub: false
last_verified: 2026-06-18
---

## Why it exists

The per-agent NarraMessenger binding card in the right-side IM Channels panel
(`IMChannelsSection`). Mirrors `TelegramConfig.tsx` shape (fetch → bind/unbind →
`onBindStateChange` fan-out to the parent badge).

## Design decisions

- **No token typing — paste a bind link.** Unlike Lark/Slack/Telegram (which
  paste raw tokens), the owner copies a one-time bind command from the
  NarraMessenger app (My Space → My Agents → Bind Agents) and pastes it; the
  backend `do_bind` drives the Gateway bind and stores the credential. So this
  card only needs one text input + Bind, plus Unbind when bound.
- Bound-state view shows `matrix_user_id` + `connection_mode` + owner, keyed on
  the sanitised `/credential` response (no bearer ever reaches the frontend).
