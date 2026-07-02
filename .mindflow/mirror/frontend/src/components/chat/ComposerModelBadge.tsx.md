---
code_file: frontend/src/components/chat/ComposerModelBadge.tsx
last_verified: 2026-06-24
stub: false
---

# chat/ComposerModelBadge.tsx — in-composer model indicator + one-click switcher

## Why it exists

The conversation model is the user's `agent` provider slot, normally edited in
Settings › Providers. Switching it mid-chat shouldn't require leaving the
composer, so this badge surfaces the current model right in the tools row and
lets the user pick another one from the same provider in a single click. It is
the chat-side convenience face of the existing slot config — it does not invent
a parallel notion of "model"; picking here is exactly the change Settings would
make. This respects binding rule #15: the platform never *chooses* a model for
the user, it only makes the user's own choice quick to reach.

## How it works / design

- Loads the `agent` slot config + that provider's available model list once via
  `api.getProviders()`; choosing a model PUTs the slot through
  `api.setProviderSlot` (optimistic update, reverts on failure) — the same
  endpoint Settings drives, so there is one source of truth, not two.
- Upstream: rendered by [[ChatPanel]] in the composer tools row. Downstream:
  the providers API (`api.getProviders` / `api.setProviderSlot`) and
  `react-router` navigation into `/app/settings`.
- When no slot is configured it degrades to a "set model" link into Settings
  rather than showing a broken/empty switcher; while loading it shows `…`.
- `prettify` trims the `provider/` prefix off model ids for display only — the
  full id is always what's persisted. Gotcha: the dropdown reads from the
  provider's `models` array, so a model the user typed manually elsewhere but
  that isn't in that list won't appear as a pickable option (only the "More in
  settings →" escape hatch covers it).
