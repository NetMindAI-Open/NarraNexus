---
code_file: frontend/src/components/awareness/TelegramConfig.tsx
last_verified: 2026-09-09
stub: false
---

# TelegramConfig.tsx — Per-agent Telegram bot binding UI

## 2026-09-09 — 把 `credential.disabled_reason` 传给 ChannelActiveToggle（B-28）

trigger 因 401（token 撤销）/ 409（另一个 poller）自动停用后，面板不再只是一个哑的
"Inactive"，而是显示后端写下的原因。

## Why it exists

The Settings → Awareness panel slot for binding a Telegram bot token to the
current agent. Collects the bot token, POSTs to the generic channel bind
endpoint, and renders bound/unbound state with an unbind action.

## Upstream / Downstream

- **Used by**: registered as the `telegram` row's `component` in
  `ui.channels` by `registerBuiltinChannels.ts` (owner
  `builtin.channels.telegram`); rendered by `IMChannelsSection` when that
  row is selected.
- **Props type**: `ChannelConfigProps` — imported directly from
  `@/platform/registries`, not re-exported through `IMChannelsSection.tsx`.
  See the 2026-09-07 entry below.
- **Calls**: `api.channel*('telegram', …)` generic channel endpoints.

## 2026-09-07 — import fixed to the registry, not a sibling component (I-8)

Previously imported `ChannelConfigProps` from `./IMChannelsSection`, which
merely re-exported the type from `@/platform/registries`. Fixed to import
straight from `@/platform/registries`; `IMChannelsSection.tsx` dropped the
re-export (same fix applied identically to all five sibling channel config
components).

## 2026-09-07 — disabling this builtin now actually works (I-2 follow-on)

`registerBuiltinChannels.ts`'s six `CHANNELS.register(...)` calls were
previously all guarded behind a single `if (!CHANNELS.has('lark'))`, so
disabling `builtin.channels.telegram` alone did not stop this row from
registering. Each row is now guarded on its own id.
