---
code_file: frontend/src/components/settings/ProviderSettings.tsx
last_verified: 2026-05-18
---

## 2026-05-18 — `authFetch` 必须发 `X-User-Id`（修跨用户写入 bug）

之前 `authFetch` 只发 JWT Bearer，不发 X-User-Id。Local 模式下 backend middleware 看到 header 缺失就 fallback 到"users 表第一行"，导致 binliang3 在 Settings 页面填的 NetMind API key 全部写到了 binliang（最老账号）名下。后端这次彻底关掉了 fallback（缺 header 直接 401），所以这里也必须配合发上来。

同时 `providerUrl()` 删除了 `?user_id=...` 这条 query 通道——和后端一致，identity 只走 header。这条提交里同步更新的还有 `App.tsx` 和 `SetupPage.tsx` 的 bare `fetch(...?user_id=...)` 调用，统一改走 `api.getProviders()`（ApiClient 自动发 X-User-Id 和 JWT）。

`syncProviderDefaults` 的签名也从 `(userId: string)` 改成 `()`——参数没意义了。


## 2026-05-14 — Quick Add auto-fills empty slots (NetMind)

`handleQuickAdd` now sends `default_slots` so a brand-new user with just
an API key is immediately usable — no manual slot wiring.

- `PRESET_DEFAULT_SLOTS` maps a preset → recommended `{protocol, model}`
  per slot. Only `netmind` is wired up: one NetMind key creates both an
  Anthropic- and an OpenAI-protocol endpoint, so all three slots fill
  from one key — `agent` → DeepSeek V4 Pro (anthropic), `helper_llm` →
  DeepSeek V4 Flash (openai), `embedding` → BGE-M3 (openai). Model ids
  must match `model_catalog.py` `DEFAULT_MODELS[("netmind", ...)]`.
- Only **empty** slots are filled — `handleQuickAdd` skips any slot that
  already has a `config`. The backend `set_slot` is an upsert, so
  including an already-configured slot would clobber the user's choice.
  This makes the feature safe for the "existing user re-adds NetMind"
  path, not just fresh signups.
- The backend hook (`POST /providers` `default_slots`) already existed
  and was dormant — no backend change; this just started sending it.
- After a Quick Add that auto-filled ≥1 slot, the `autoConfigured`
  state drives a confirmation `Dialog` ("You're ready to go") listing
  what was set and pointing at the slot section for overrides.

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

## Claude Code Login card — two decoupled state layers

The card surfaces two state layers that must NOT be conflated:

1. **OS credential state** — owned by the `claude` CLI, persisted in
   `~/.claude/.credentials.json`. Drives the Login / Re-login / Logout
   buttons. Backed by `/api/providers/claude-status` (which calls
   `claude auth status` + falls back to the credentials file) and the
   Tauri IPC commands `trigger_claude_login` / `trigger_claude_logout`.
2. **Provider record state** — owned by NarraNexus, persisted in
   `user_providers` (rows where `source='claude_oauth'`). Drives the
   "Add as Provider" / "Remove" affordance and `hasClaude`.

Earlier versions wrapped the entire login UI in `!hasClaude`, which
hid Login/Logout once a provider record existed. That broke account
switching, post-expiry re-auth, and even just seeing which account is
active. Decoupling the two layers means a user can re-login or sign
out without first deleting the provider record — and conversely, can
add/remove the provider without touching OS credentials.

Symmetric end-to-end: backend exposes `email` and `expires_at` in
`claude-status`; the helper `formatExpiresAt()` accepts ISO-8601 or
unix epoch (sec or ms) since the CLI shifts schema across versions.

## Login auto-abort timer

`claude auth login` blocks until the user finishes (or abandons) the
OAuth flow in the browser. Earlier the Tauri command awaited
indefinitely — closing the browser tab without authorizing left the
CLI sitting on a dead callback server forever, with the UI button
stuck on "Logging in...".

Now the Login flow runs a `CLAUDE_LOGIN_TIMEOUT_SEC = 600` countdown:
- `handleClaudeLogin` sets `claudeLoginRemaining` to 600 alongside
  starting the IPC.
- A `useEffect` decrements every second via `setTimeout` (not
  `setInterval`, to avoid the standard "fires while previous handler
  is still pending" trap).
- On hitting 0 the effect fires `cancelClaudeLogin()` → Rust SIGTERMs
  the child → trigger's await resolves with non-zero exit →
  handleClaudeLogin's catch+finally clears UI state.
- The remaining seconds are rendered as `m:ss` inside the Login /
  Re-login button label.

The countdown state is intentionally cleared by handleClaudeLogin's
finally (NOT by the timer effect) so it's authoritative — natural
completion, manual cancel, or timeout all funnel through the same
reset path.

## Gotchas

- This file is large (~400 lines) because it manages five distinct async
  operations with their own loading/error states. Each operation is
  intentionally inline rather than extracted to keep the request/response
  flow readable in one place.
- Model lists are fetched per-provider on demand (when the user expands a
  provider). Caching is local state — refreshing the page re-fetches.
- `getApiBaseUrl()` from `runtimeStore` ensures the correct backend URL is
  used whether running locally or in Tauri mode.
- **`ModelBubbleInput` commit trap** — text typed in the tag input is only
  pushed into `formModels` on Enter / `+` click. If the user types a model
  name and clicks "Add Provider" without committing, the text is silently
  lost and the backend autopopulates defaults (2 Claude models for
  `anthropic` card_type). As of 2026-04-23 the input shows a warning hint
  and pulses the `+` button while uncommitted text exists, to make the
  commit step visible. A stronger fix (auto-flush on submit) was deferred.
