---
code_file: frontend/src/lib/netmindAuth/useNetmindAuth.ts
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 — 登录失败模糊文案（防账户枚举，Mark item 2）

`emailLogin` 的第一段（浏览器直连 NetMind `/user/emailLogin`）catch 里,**区分两类失败**（复审 item 1）:只有**上游明确拒绝**（`e instanceof NetmindApiError`,即信封 `success:false`,见 [[request.ts]]）才遮成 `i18n.t('pages.login.invalidCredentials')`——它对「user not found」vs「wrong password」给不同文案可枚举邮箱;而**传输失败**（断网/DNS/502 网关页,裸 `TypeError`/`SyntaxError`）走 `connectionFailed`,不能把「服务挂了」误报成「你密码错了」。**真实上游 message 两类都照旧进 `api.reportAuthFunnel`**。⚠ 根因在 NetMind 侧（文案是它给的）;这里是客户端遮蔽。MS OAuth 过多 scope（item 4）属 NetMind auth.html 构造,本仓改不了,未动。`useNetmindAuth.test`/`useNetmindAuth.emailLogin.test.ts` 覆盖两分支（credential 遮蔽 + transport 显 connectionFailed + 漏斗拿到原文）。

**复审三轮 item 3——忘记密码枚举收口（遮文案不够,还得遮"结果"）**:`sendResetCode` 改**恒定成功语义**——上游拒绝(`NetmindApiError`,如"email not registered")也 `return true`+清 error 让 UI 照常推进到验证码步骤,只有**传输失败**才 `return false`+`connectionFailed`；否则"是否推进到下一步"这个布尔比文案更好自动化枚举。`resetPassword` 的上游拒绝**一律遮成同一句** `pages.login.resetCodeInvalid`（"验证码无效/过期,请重取"——把"wrong code"与"email not registered"抹成同一句,可行动又不泄存在性）,transport→`connectionFailed`。`handleAuthCallback`/`submitBind` 保 transport 分流。`exchange`(自家后端)原样。funnel 仍留痕(`netmind_reset_code_failed`,后端白名单已在同 PR 补,见 [[auth]]）。**残留**:攻击者直连 NetMind 端点仍可枚举——那是 NetMind/后端统一响应的活,与 send-code follow-up 同批。`resetCodeInvalid` 新键 10 locale 齐。

**复审三轮修正**:①`resetPassword` 的一刀切遮蔽会连「密码不符策略」也抹成「验证码无效」→重置死循环;修法是 [[ForgotPasswordCard.tsx]] 提交前客户端校验密码([[passwordPolicy.ts]]),于是到 `resetPassword` 的上游拒绝只剩码错/邮箱不存在,`resetCodeInvalid` 遮蔽才诚实;并补 `api.reportAuthFunnel('netmind_reset_password_failed', …)`(这一步原本零留痕)。②`!data.loginToken` 两处(emailLogin/submitBind)**改回裸 `Error`**(不是上轮的 `NetmindApiError`)——协议破损走 `connectionFailed`(「重试」),而非 `invalidCredentials`(会把用户推去改没错的密码,正好撞死循环)或裸英文 `Bind failed`。判据:`NetmindApiError` 只表示「信封 success:false 的上游拒绝」,协议破损不是。

## 2026-07-13 — desktop OAuth path (Tauri bridge + poll)

`startOAuth` branches on `isTauri()`: browser keeps `window.open` + the `message`
listener; desktop calls `openNetmindOAuth(url)` ([[tauri.ts]] →
[[netmind_oauth.rs]]) then **polls** `takeNetmindOAuthResult` every 800ms
(≤3 min) for the bridged `{code,state}`, decodes via `decodeOAuthPayload`
(handles both URI-encoded and plain JSON), and resumes `handleAuthCallback`
through `handleAuthCallbackRef` (kept current since startOAuth is defined before
handleAuthCallback). Poll interval tracked in `oauthPollRef` (cleared on second
click + on unmount). **Why poll, not a Tauri event**: the first DMG test showed
the OAuth child window closing (Rust bridge fired) but the app staying on the
login page — a live `listen` needs `window.__TAURI__` (withGlobalTauri is off
here) and silently no-ops; `invoke`-based polling always works.
`sessionStorage['nm-oauth-type']` is still set in the main window, so
handleAuthCallback reads it unchanged. Only relevant once local dual-mode Power
login is enabled (else no OAuth buttons show).

## 2026-06-12 — forgot-password methods (sendResetCode + resetPassword)

Two methods added for NetMind's own password reset (cloud passwords ARE NetMind
passwords, so no backend): `sendResetCode(email)` → POST `/register/sendCode`
with `type: 2` (forgot-password path; no reCAPTCHA there — NOTE: send-code lives
under `/register/`, only the reset itself is under `/user/`), and
`resetPassword(email, code, newPassword)` → POST `/user/resetPassword`. Both
call NetMind directly via `netmindPost` like emailLogin, share the hook's
loading/error state, and return a boolean so the UI ([[ForgotPasswordCard.tsx]])
can advance step / close. Exposed on the hook's return.

# useNetmindAuth.ts — NetMind login orchestration hook

## Why it exists

The cloud login page needs to support three distinct entry points — email/password,
OAuth popup (Google/Microsoft/GitHub), and bandType binding (when a third-party
OAuth account needs to be linked). All three paths ultimately produce a NetMind
`loginToken` that must be exchanged for our own JWT via `api.netmindLogin`. This
hook centralises that convergence so the login page component does not need to
know about the internal choreography.

Without this hook, each entry path would independently manage loading/error state,
call `netmindPost` with different endpoint shapes, and then duplicate the
`api.netmindLogin` exchange call — a maintenance problem as soon as any of the
three paths evolves.

## What this file does NOT do

It does not render anything — it is a pure React hook. It does not perform
reCAPTCHA validation; `ckType=2` in the email login body tells NetMind to skip
it. It does not persist the resulting JWT — that is the caller's responsibility
(the `onSuccess` callback receives both the backend response and the raw
`loginToken` so the caller can store what it needs for Phase 2/3). It does not
open or manage the OAuth popup window beyond calling `window.open`; the popup
communicates back via `window.postMessage`.

## Upstream / downstream

- **Used by**: the cloud login page component (not yet created), which renders
  email/password fields, OAuth buttons, and optionally a bind confirmation dialog.
  The component wires `onSuccess` to update auth context / redirect.
- **Depends on**:
  - `./request` (`netmindPost`) for all calls to the NetMind auth API
  - `./constants` (`baseRequestParams`) to inject the required boilerplate fields
  - `./crypto` (`encryptPassword`, `generateRandomString`) for DES-encrypting the
    password before sending
  - `./types` (`AuthBindInfo`, `NetmindUser`) for the bind-flow state shape
  - `@/lib/api` (`api.netmindLogin`) to exchange the NetMind loginToken for our JWT
  - `@/lib/runtimeConfig` (`getNetmindConfig`) to obtain `accountsUrl` and `authApi`
    for building the OAuth popup URL
  - `@/types/api` (`NetmindLoginResponse`) as the type of the `onSuccess` first arg

## OAuth popup flow

`startOAuth(type)` opens a popup window pointing to NetMind's `auth.html`. The
popup completes OAuth and calls `window.opener.postMessage({type:'auth', code, state})`.
The hook registers a `message` event listener on mount (cleaned up on unmount)
that intercepts this message and calls `handleAuthCallback(code, state)`. Callers
can also call `handleAuthCallback` directly (e.g. from a redirect-based OAuth flow
where the callback lands on a dedicated route).

## Bind flow

When `handleAuthCallback` gets a response without `loginToken`, it means NetMind
needs the user to confirm or supply an email before completing the link. The hook
surfaces this as `bindInfo` state. The login page renders a bind dialog; the user
submits, triggering `submitBind`. After a successful bind, `bindInfo` is cleared
and the `loginToken` exchange proceeds normally.

## Design decisions

- **`onSuccess` receives both `res` and `loginToken`**: the caller needs the raw
  `loginToken` to stash for Phase 2 (credits) and Phase 3 (API key generation).
  Passing only the backend response would require a second round-trip or an
  out-of-band store.
- **`ckType: 2` — no reCAPTCHA**: NetMind supports multiple check types; type 2
  skips the captcha challenge. This is intentional: NarraNexus runs behind its own
  login gate and does not need captcha on top.
- **`sessionStorage` for OAuth type**: the popup opens in a separate browsing
  context; the easiest way to remember which OAuth provider was selected so
  `userCallBack` receives the correct `oauthType` is to write it before opening the
  popup and read it back in the callback. `localStorage` would persist across tabs
  undesirably; `sessionStorage` is scoped to the tab.

## Gotcha / boundary cases

- **Trigger**: calling `startOAuth` in a test environment (or a strict popup
  blocker) without mocking `window.open`.
  **Symptom**: the test either throws on `window.open` being undefined or the popup
  is silently blocked.
  **Root cause**: `window.open` is a browser primitive; tests need to `vi.spyOn`
  it. The hook does not guard against a blocked popup — the message listener simply
  never fires.

- **Trigger**: the `message` event fires from a cross-origin source (e.g. an iframe
  or unrelated popup) that happens to include `{type:'auth', code, state}`.
  **Symptom**: `handleAuthCallback` is invoked with attacker-controlled `code` and
  `state`.
  **Root cause**: there is no `e.origin` check in the listener. A future hardening
  step should validate `e.origin === getNetmindConfig().accountsUrl`.

- **Trigger**: `netmindPost` is called but `getNetmindConfig().authApi` is `""` (no
  runtime config in dev).
  **Symptom**: the request hits a relative path on the NarraNexus backend, gets a
  404, and the error surfaces as `"Login failed"`.
  **Root cause**: inherited from `request.ts` — see its Gotcha for details.

## New-developer traps

- The hook only exports named functions; it has no default export. Import as
  `import { useNetmindAuth } from './useNetmindAuth'`.
- `submitBind` is a no-op if `bindInfo` is `null`. Don't call it before
  `handleAuthCallback` has set the bind info.
- `closeBind` clears `bindInfo` without triggering any backend call — it is purely
  a UI cancel action.

## Related constraints

- Iron Law #3 (module independence) — this hook must not import from other
  netmindAuth modules except the four peer files (`request`, `constants`, `crypto`,
  `types`) which are its direct and intentional dependencies.
- See `references/phase1-frontend-login-migration.md` for the full Phase 1 login
  migration design, including the three-path convergence rationale and the
  `loginToken` stashing contract for Phases 2 and 3.

## 2026-08-05 — 直连 NetMind 的失败会上报服务端（reportAuthFunnel）

emailLogin 与 handleAuthCallback 是浏览器直连 NetMind 的——失败只存在于用户
的 tab 里,服务端连「有人尝试过登录」都不知道（8/1 活动「注册成功后无法登录」
不可诊断的根因）。两处 catch 现在在 setError 之外补一发
`api.reportAuthFunnel(stage, email?, message)`,fire-and-forget、绝不 throw、
绝不影响用户看到的错误。成功路径零上报（有测试钉住）。

### 2026-08-05 R2（review 修正）：上报范围收窄到「服务端看不见的那一段」

R1 的 catch 罩太宽（review 指出）：emailLogin 的 catch 同时罩住 ①直连
NetMind、②exchange（打我们后端,服务端已落 [login-funnel]）、③onSuccess
回调——②被双记且贴错标签,③把成功登录记成失败。修正：拆双 try,只有
直连 NetMind 的那段失败才 reportAuthFunnel;exchange 失败只 setError
（服务端已有记录）。handleAuthCallback 同款拆分。测试钉住：exchange
失败必须 0 上报。

### 2026-08-05 R3：handleAuthCallback 的拆分补上对称测试

R2 的 mirror 声称「测试钉住」但只有 emailLogin 有用例（review 指出）。补齐：
OAuth 直连失败→上报 netmind_oauth_failed;OAuth 成功但 exchange 失败→只
setError 零上报（recvre9LlfwXAP 要区分的那个桶,不能错标）。
