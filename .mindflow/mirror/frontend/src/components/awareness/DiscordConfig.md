---
code_file: frontend/src/components/awareness/DiscordConfig.tsx
stub: false
last_verified: 2026-06-16
---

## Why it exists

The Discord card in the Awareness panel's IM Channels section — bind /
test / unbind a Discord bot for the active agent. Cloned from
``TelegramConfig.tsx`` (single token, two states: unbound form / bound
status).

## Design decisions

- **Single Bot Token + optional numeric owner user id.** No OAuth dance;
  the disclosure walks the Developer Portal flow.
- **Message Content Intent is called out prominently** (yellow REQUIRED
  marker) — it's the one manual step that silently breaks the bot (empty
  message bodies) if missed. The disclosure also explains the
  blank-message and no-reply-in-server symptoms.
- **No owner "pending" state** (unlike Telegram). Discord resolves the
  owner's display name at bind time from the numeric id, so the bound
  state is binary: owner registered or not.
- **Icon: lucide ``Bot``** (no official Discord glyph in lucide).

## Upstream / downstream

- **Upstream**: ``ChannelConfigProps`` from ``IMChannelsSection.tsx``;
  ``api.getDiscordCredential`` / ``bindDiscordBot`` /
  ``testDiscordConnection`` / ``unbindDiscordBot`` from ``lib/api.ts``;
  ``DiscordCredentialData`` from ``types/api.ts``.
- **Registered in**: the ``IM_CHANNELS`` array in ``IMChannelsSection.tsx``.

## Gotchas

- Client-side owner-id validation is numeric-only (defensive — backend
  re-validates). The token field is ``type="password"`` and never echoed.
