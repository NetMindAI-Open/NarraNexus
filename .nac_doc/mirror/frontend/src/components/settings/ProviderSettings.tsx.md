---
code_file: frontend/src/components/settings/ProviderSettings.tsx
last_verified: 2026-04-10
---

# ProviderSettings.tsx — LLM provider CRUD and model-slot assignment

The most complex settings component. Manages two sections:
1. **Provider list** — add (Anthropic, OpenAI, or custom URL), remove,
   show masked API keys.
2. **Model assignment** — three slots (Agent, Embedding, Helper LLM) each
   with a provider + model picker. Changes are staged locally and applied or
   discarded together.

## Why it exists separately from SettingsModal

Provider configuration is stateful (API calls, local form state, multiple
async operations). Keeping it in its own file lets `SettingsModal` stay as a
thin shell and makes provider logic independently testable.

## Upstream / downstream

- **Upstream:** backend REST endpoints under `/api/providers/` and
  `/api/models/` — all called via raw `authFetch` (not the `api` lib)
- **Downstream:** embedded in `SettingsModal` Providers section
- **Auth:** `authFetch` reads the JWT token from localStorage for cloud mode

## Design decisions

**`authFetch` wrapper:** Injects the JWT Bearer header when a token exists in
localStorage. This is how cloud-mode auth works — the same component runs in
both local and cloud mode without branching.

**Staged model assignment:** Users pick Agent/Embedding/Helper models into
local state and explicitly click Apply. This avoids partial saves if the user
changes their mind mid-way.

**Protocol filter on model slots:** The Embedding slot only shows models from
providers with `OpenAI` protocol (embedding API format). The Agent slot only
shows models from providers with `Anthropic` protocol. This prevents the user
from accidentally assigning a chat model to the embedding slot.

## Gotchas

- This file is large (~400 lines) because it manages five distinct async
  operations with their own loading/error states. Each operation is
  intentionally inline rather than extracted to keep the request/response
  flow readable in one place.
- Model lists are fetched per-provider on demand (when the user expands a
  provider). Caching is local state — refreshing the page re-fetches.
- `getApiBaseUrl()` from `runtimeStore` ensures the correct backend URL is
  used whether running locally or in Tauri mode.
