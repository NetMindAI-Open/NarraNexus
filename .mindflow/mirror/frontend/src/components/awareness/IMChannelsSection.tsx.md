---
code_file: frontend/src/components/awareness/IMChannelsSection.tsx
last_verified: 2026-09-07
stub: false
---

# IMChannelsSection.tsx — three-level disclosure shell for IM channel bindings

## Why it exists

The single Awareness-panel entry point for all IM channel bindings
(Lark/Slack/Telegram/WeChat/NarraMessenger/Discord, plus any channel plugin
adds its own). Renders the collapsed → expanded → one-card-open disclosure
and the connected-count badge; the actual per-channel bind/unbind forms live
in each channel's own config component.

## This file doesn't do

It is not a registry of channels — that's `ui.channels` (`@/platform/registries`),
populated by `registerBuiltinChannels.ts` for the six builtins and by
individual channel plugins for third-party ones. This file only reads
`sortedChannels(useRegistryEntries(CHANNELS))` and renders whatever rows are
currently registered.

It also does not define `ChannelConfigProps` — that type lives in
`@/platform/registries`. See the 2026-09-07 entry below.

## Upstream / Downstream

- **Used by**: the Awareness settings panel.
- **Depends on**: `ui.channels` registry (`@/platform/registries`); imports
  `./registerBuiltinChannels` for its side effect (registering the six
  builtin rows) — the import itself is otherwise unused.
- **Each rendered channel card**: renders that row's `component`, one of
  `LarkConfig` / `SlackConfig` / `TelegramConfig` / `WeChatConfig` /
  `NarramessengerConfig` / `DiscordConfig`, or a channel plugin's own
  component — all of which import their `ChannelConfigProps` type from
  `@/platform/registries` directly.

## 2026-09-07 — stopped re-exporting `ChannelConfigProps` (I-8)

Previously this file `export type { ChannelConfigProps } from '@/platform/registries'`
and the five builtin config components imported the type from here instead
of from the registry module that actually declares it. That made this file
an unintended type hub: a channel plugin authored outside this app package
had no importable path to the shared props type (it can't import a private
app component), and a future refactor of this file could silently change
the type five other files depend on. The re-export is gone; every consumer
now imports `ChannelConfigProps` straight from `@/platform/registries`.
