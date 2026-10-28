---
code_file: frontend/src/platform/registries/channels.ts
last_verified: 2026-09-04
stub: false
---

# registries/channels.ts — ui.channels

## Intent

The rows of the Channels section as a registry (label, icon, config component, status probe, order). Builtins register in `components/awareness/registerBuiltinChannels.ts` with their plugin id as owner (so `disableBuiltinUi` removes the row); a channel plugin registers its own row (batch 4b brings the schema-driven `GenericChannelConfig`). `ChannelConfigProps` lives here and is re-exported from `IMChannelsSection` so the six config components keep their import path.
