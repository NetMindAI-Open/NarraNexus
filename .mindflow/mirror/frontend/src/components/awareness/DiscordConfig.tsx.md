---
code_file: frontend/src/components/awareness/DiscordConfig.tsx
last_verified: 2026-09-09
stub: false
---

# DiscordConfig.tsx — Per-agent Discord bot binding UI

## 2026-09-09 — 把 `credential.disabled_reason` 传给 ChannelActiveToggle（B-28 I1）

trigger 因永久上游失败自动停用后，面板显示后端写下的（已脱敏截断）原因，而不是哑的 Inactive。

## Why it exists

The Settings → Awareness panel slot for binding a Discord bot token to the
current agent. Collects the bot token (+ optional guild allowlist), POSTs to
the generic channel bind endpoint, and renders bound/unbound state with an
unbind action.

## Upstream / Downstream

- **Used by**: registered as the `discord` row's `component` in
  `ui.channels` by `registerBuiltinChannels.ts` (owner
  `builtin.channels.discord`); rendered by `IMChannelsSection` when that row
  is selected.
- **Props type**: `ChannelConfigProps` — imported directly from
  `@/platform/registries` (the registry module that declares the shape all
  six channel config components share), not re-exported through
  `IMChannelsSection.tsx`. See the 2026-09-07 entry below for why that
  matters.
- **Calls**: `api.channel*('discord', …)` generic channel endpoints.

## 2026-09-07 — import fixed to the registry, not a sibling component (I-8)

Previously imported `ChannelConfigProps` from `./IMChannelsSection`, which
happened to re-export the type from `@/platform/registries`. That indirection
made `IMChannelsSection.tsx` a load-bearing type hub for five sibling files
even though the type is not actually defined there, and meant a plugin
author who wants to register a seventh channel config component (in a
different package, unable to import a private app component) had no
importable path to the props type. Fixed by importing straight from
`@/platform/registries`; `IMChannelsSection.tsx` dropped the re-export.

## 2026-09-07 — disabling this builtin now actually works (I-2 follow-on)

`registerBuiltinChannels.ts` used to guard all six `CHANNELS.register(...)`
calls behind a single `if (!CHANNELS.has('lark'))`. Disabling
`builtin.channels.discord` alone (`disableBuiltinUi`) didn't stop this file's
row from registering, because the shared guard only checked the Lark id. Each
row is now guarded on its own id, so `disableBuiltinUi('builtin.channels.discord')`
correctly prevents the Discord row (and this component) from ever showing up
in `IMChannelsSection`.
