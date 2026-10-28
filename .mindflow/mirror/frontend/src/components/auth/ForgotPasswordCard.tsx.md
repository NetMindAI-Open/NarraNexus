---
code_file: frontend/src/components/auth/ForgotPasswordCard.tsx
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — the 6 remaining hardcoded strings localized (GitHub #107)

"Password updated" / its body / "Back to sign in" / "Reset password"
(heading, reused for the submit button) / "Cancel" / "Send code" were
still literal English — everything else in this file already went
through `t()`. Added `pages.login.resetPasswordDone(Body)`,
`backToSignIn`, `resetPasswordTitle`, `sendCode`,
`verificationCodeLabel`, `newPasswordLabel` (all 10 locales); `Cancel`
reuses the existing `common.cancel` key and the Email field reuses
`pages.login.emailLabel` rather than adding synonyms. The two field
placeholders that duplicated their own label text now reuse the same
label key instead of carrying a second hardcoded string; the
`you@example.com` placeholder stays as-is (a format example, not
language-bearing content — same treatment other email inputs in this
codebase already get). No behavior change: `ForgotPasswordCard.test.tsx`
matches on the same English text via `t()`'s English fallback, so it
would fail here if any of the new keys were missing from en.json.

## 2026-08-12 — 客户端密码校验 + 换邮箱 + 恒定成功配套（复审三轮）

第二步新增**实时密码规则清单**（复用 [[passwordPolicy.ts]] 的 `PASSWORD_RULES` + `pages.signup.rule.*` 文案）,Reset 按钮 `disabled` 加 `failedRules.length===0`——**策略违规密码根本发不出请求**,这样 [[useNetmindAuth.ts]] `resetPassword` 的 `resetCodeInvalid` 遮蔽不会掩盖可修的弱密码(否则死循环,见 passwordPolicy 说明)。配合 `sendResetCode` 恒定成功语义:加「换个邮箱」按钮(`useDifferentEmail`,置回 `codeSent=false`)让输错邮箱的用户能回退;文案改中性 i18n 键(`forgotResetIntro`/`forgotCodeSentHint`),不再暗示「一定发出去了」。卡片其余仍硬编码英文(既有欠账,未动)。

# ForgotPasswordCard.tsx — cloud-mode password reset modal

## 为什么存在

云端版的密码就是 NetMind 账号的密码，所以"忘记密码"直接驱动 NetMind 自己的重置流程，不经过我们的后端。这个组件是一个模态弹窗，把 NetMind 的两步重置（`sendCode` type=2 发邮箱验证码 → `resetPassword` 用验证码+新密码）收进一个界面：第一步输邮箱发码，第二步输验证码+新密码完成重置，成功后提示回登录。

## 上下游关系

**被谁用**：[[LoginPage.tsx]] cloud 分支——点 "Forgot password?" 链接（`showForgot` state）时渲染。
**依赖谁**：[[useNetmindAuth.ts]] 的 `sendResetCode` / `resetPassword`（前端直调 NetMind，无后端）；`@/components/nm` 的 FormField / TextInput / Button。

## 设计决策

- **独立弹窗，不内嵌进登录表单**：与 CreateUserDialog / AuthBindDialog 一致的弹窗模式，登录卡片结构不动。
- **两步在一个弹窗内**（`codeSent` state 切换），而不是跳外部 NetMind 页——重置接口无 reCAPTCHA，前端直调成本低、体验更连贯。

## Gotcha / 边界

- NetMind 后端 `/user/resetPassword` 对 `THIRD_USER`（第三方来源）账号有 `THIRD_NOT_UPDATE` 限制——但**已与 Power 确认我们代建/迁移的账号不是 `THIRD_USER` 来源**（2026-06-12），所以 resetPassword 对它们可用，忘记密码流程对迁移用户有效。
