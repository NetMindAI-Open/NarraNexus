---
code_file: frontend/src/components/auth/SignUpDialog.tsx
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 — Mark 前端批：弹窗 Esc/背景关闭 + 改邮箱重置验证码

- **Esc/背景关闭（item 3）+ in-flight/拖拽保护（复审 item 4）**：此前只有 X。加 `document` keydown（Escape→onClose）。背景关闭改用 **`onMouseDown` 记起点 + `onClick`**——两者都必须落在遮罩本身(`e.target===e.currentTarget`)才关,否则「在输入框按下选文字→拖到卡片外松开」会被误判成背景点击而关窗。**所有关闭路径(Esc/背景/X)都加 `busy = sending||submitting` 守卫**:请求在飞时不关(否则组件卸载→后续 setError 落空→弹窗凭空消失、验证码白烧)。`busy` 进 Escape effect 依赖。
- **改邮箱重置验证码状态（item 7）**：email 的 onChange 走 `onEmailChange`——已发过码（`codeSent||cooldown>0||code`）就清 `codeSent/cooldown/code`。
- **sendCode 不回显上游文案 + 保留诊断（复审二轮 item 1）**：catch 用 `t('pages.signup.sendFailed')` 不透传 NetMind「该邮箱已注册」(防枚举),但**不再裸 `catch {}` 丢掉根因**——`api.reportAuthFunnel('signup_send_code_failed', email.trim().toLowerCase(), message)` 留痕(传输失败根本没到服务端,funnel 是唯一痕迹;只带 email+message,绝不带验证码——文件头铁律)。⚠仅遮 UI;**彻底关闭注册枚举需后端 `/register/sendCode` 返统一响应,记为 follow-up**。
- **背景关闭改判在 `onMouseUp`（复审二轮 🟢）**:mousedown+mouseup **两端都必须落遮罩**才关,堵住「遮罩按下→卡片内松手」这个镜像手势(之前只堵了反向)。
见 `SignUpDialog.test.tsx`（Esc/背景 press-release/拖拽不关/点内部不关/in-flight Esc 不关/改邮箱重置）。
- **`PASSWORD_RULES` 抽到 [[passwordPolicy.ts]]（复审三轮）**：本地定义删除,改 import 共享副本——[[ForgotPasswordCard.tsx]] 现在也用同一份,消除「同一后端策略抄两处/另一处根本没有」。规则/文案/UI 不变。

# SignUpDialog.tsx — 在我们自己的页面上注册

## 它替掉了什么

原来「Create account」是一个 `<a target="_blank">` 跳到 netmind.ai,把用户在流程
中途交出去;回来(如果回来了)只看到一个登录框,没有任何交代。用户反馈的就是
这个。

## 为什么不做成分步向导

三个字段而已。唯一的顺序约束是「验证码必须先请求才能填」,其余随便填 —— 一屏
全露出来,正是让这个表单显得短的原因。

## 它不负责登录

拿到「账号已创建」之后,把凭据交回登录页,由页面既有的 `netmind.emailLogin`
去建立会话。**建立会话的逻辑只有一处**;在这里再写一份就是第二个会漂移的地方。

## 验证码那个字段的坑

`FormField` 会把 label 的 `htmlFor` id 注入给它的**第一个子元素**。所以把
输入框和「发送」按钮一起包进一个 `<div>`,label 就指到了 div 上 —— 一个
什么都没标注的 label。按钮必须放在 FormField **外面**。

(这不是理论问题:测试里 `getByLabelText(/verification code/i)` 直接报
「element associated with this label is non-labellable」把它抓了出来。)

## 密钥

密码和验证码只存在组件 state 里、只传给 `api.*`。不进日志、不进 URL、不进埋点。
