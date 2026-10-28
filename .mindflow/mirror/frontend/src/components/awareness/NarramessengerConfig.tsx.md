---
code_file: frontend/src/components/awareness/NarramessengerConfig.tsx
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — import fixed to the registry, not a sibling component (I-8)

`ChannelConfigProps` is now imported directly from `@/platform/registries`, not from
`./IMChannelsSection` (which merely re-exported the type — see that file's mirror doc). Same fix
applied identically to the other five channel config components.

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

## 2026-09-04 · generic channel API (batch 4d.3)

Calls `api.channel*('narramessenger', …)` with the channel's own typed envelopes; the bind body is the descriptor's `bind_fields` as a `fields` object. UI and flow unchanged.
