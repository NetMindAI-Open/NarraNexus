---
code_file: frontend/src/components/settings/QuotaPanel.tsx
stub: false
last_verified: 2026-07-07
---

## 2026-07-07 — toggle 解锁:用尽时只禁「开」不禁「关」(#48)

「Use free quota」勾选框原为 `disabled={exhausted}` —— 用尽时**两个方向都锁**。
若用尽时偏好恰为 ON,用户无法取消勾选切到自己的 provider → 死锁 402(#48
reporter 的直接投诉)。改为 `disabled={exhausted && !preferSystem}`(代码里
`freeTierLocked`):用尽时只禁止「开启」(开启需预算),永远允许「关闭」——
与后端 `set_preference` 的 "OFF always allowed" 对齐。exhausted 文案拆成
`exhaustedPreferOn`(叫用户关掉切自己 key)/ `exhaustedPreferOff`(已在用自己
key,额度恢复后可重开)两条 i18n key。

# Intent

Compact read-only panel that surfaces the signed-in user's system
free-tier token quota above the provider configuration section.
Deliberately renders nothing (returns null) when:

1. The app is running in local mode (`useRuntimeStore.mode !== 'cloud'`)
2. The backend reports the feature disabled (`{enabled: false}`)
3. The quota row exists but status is `uninitialized` is still rendered,
   but as a plain text hint — no progress bars.

So local users and cloud users whose feature is off never see any
quota-related UI — no layout shift, no "feature off" copy.

## Why mounted in ProviderSettings

The user's mental model: "This is where I configure my LLM access."
Placing the free-tier budget above the provider list reinforces
"Here's what you have; here's how to go beyond it." The panel is
also inside the Settings modal, which is where users go when things
run out.

## Upstream
- `api.getMyQuota()` — single GET on mount. No polling; the panel is
  a snapshot, not a live meter (users don't burn tokens fast enough
  to notice staleness in the settings modal).

## Downstream
- `useRuntimeStore` — local vs cloud mode check
- Tailwind / CSS variables of the bioluminescent terminal design system

## UX change: exhausted toggle locked (#48)

When the quota is exhausted, the "use free quota" toggle is **disabled**
(locked, greyed out) — users cannot re-enable it while budget is zero.
The copy in that state says the user's own configured provider takes over
automatically on exhaustion (no manual unchecking required). The toggle
re-enables once the quota is replenished. This matches the backend
`QuotaPreferenceLocked` guard in `quota_service.set_preference` and the
PATCH /me/preference → 409 response; the frontend lock is the first line
of defence so the 409 is a backstop users should rarely hit.

## Gotchas
- We wait for `loaded` before deciding to render null vs content so
  the component doesn't flash visible-then-gone on mount.
- The `exhausted` state recolours bars to `--accent-error` and shows
  a hint to configure the user's own provider. Hint is positional
  rather than a modal — staff don't want to intercept users mid-flow.
- Progress bar math uses `max(0, min(100, ...))` via `pct()` so
  post-concurrent-overdraw states (used slightly > total) still look
  sensible.
