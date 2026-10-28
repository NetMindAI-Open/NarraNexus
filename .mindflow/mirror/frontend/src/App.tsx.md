---
code_file: frontend/src/App.tsx
last_verified: 2026-09-10
stub: false
---

## 2026-09-10 — 熔断横幅轮询抽成 `useCircuitBannerAutoClear`，事件 handler 去重

2026-09-09 的轮询 `useEffect` 依赖整个 `circuitOpen` 对象，而事件 handler 每帧都
`setCircuitOpen(detail)` 建新对象——用户每重试一次 interval 就重置一次，30s 永远数不
满；也没有登录门禁与登出清横幅。现在：handler 对"同 agent + 同 reason"短路（函数式
setState 返回 prev）；轮询逻辑整体搬到 [[useCircuitBannerAutoClear]]（依赖收窄为
agentId/reason/isLoggedIn、挂上即查、未登录清横幅不轮询），App.tsx 只剩一行调用。
横幅文案与 Resume 按钮不变。

## 2026-09-09 — 熔断横幅显示期间轮询状态，自愈后自动关闭（GitHub #117）

`circuitOpen` 横幅原本纯事件驱动（只在 wsManager 收到 fresh-run 拒绝帧时 set，
从不重新校验）——后端半开探测（[[circuit_breaker]]）自己好了，或 owner 在别的
tab/设备确认了凭据，这个横幅仍会挂在屏幕上直到用户手动点 Resume 或刷新页面。
加轮询 `api.getAgentCircuitBreaker`（既有、之前零调用方的端点）,
`shouldClearCircuitBanner(status.cb_status)` 判定为真就 `setCircuitOpen(null)`。
cooling 原因的横幅不轮询。实现细节以 2026-09-10 条为准。

## 2026-09-09 — the four global banners speak the user's language (GitHub #107/#108)

Session-expired, session-expiring-soon, the circuit-breaker banner
(3 message variants + Resume/Resuming + the dismiss aria-label), and the
free-tier-switch banner were all hardcoded English strings — this file
had never imported `useTranslation` at all. Added the hook + a new
`appBanners.*` top-level i18n namespace (all 10 locales; sibling
precedent: `webAnalyticsNotice` / `telemetryNotice`, both flat top-level
notice namespaces rather than nested under `pages`). `sessionExpiringSoon`
interpolates `{{time}}` (the pre-formatted `formatExpiryDistance` output)
rather than concatenating, so word order can vary per language. Not
folded into `dashboard.banners.*` — that namespace already means the
per-agent AttentionBanners dismiss control (see that file's mirror);
reusing it here would have made two unrelated banner systems share one
i18n namespace name.

## 2026-09-08 — 挂载 `usePluginTheme()`

与 `useTheme` 并列：持久化的插件主题在其插件激活注册后自动生效。

## 2026-09-08（本地 E2E 实测）— `/app/x/*` 的插件页保持路由

`/app` 布局下新增 `x/*` → `PluginPagePending`：插件 boot 未结束时渲染 `PageFallback`，结束后仍无匹配才 `Navigate` 到 chat。
已注册的 `x/<page>` 静态段永远比 `x/*` 优先，所以已加载的插件页不受影响；`appRoutes.test.tsx` 钉住「x/* 走布局保留
?next=」与「hold 等 boot 结束才离开」。

## 2026-09-07（批 1 三轮复审移植）— `<Routes>` 抽成导出的 `AppRoutes`

路由骨架（注册表页面 + 受保护的 `/app` 布局与 index 重定向 + 根重定向 + 兜底）抽成 `export function AppRoutes()`，
`App` 只在横幅/错误边界/Suspense 里渲染它。目的：`src/__tests__/appRoutes.test.tsx` 能在不挂载横幅与 store 副作用
的前提下证明「壳渲染的就是 `PAGES`」——删掉 `{pageRoutes.top}` 或 `{pageRoutes.app}` 该测试即红（此前没有任何测试
碰 `App.tsx`）。

## 2026-09-03 — 创建工作室入口路由

新增 `agents/new` → [[ChooseCreateMethodPage.tsx]]，与 `teams/new` 同层的
lazy 路由。这一页**自己不创建任何东西**（创建在下一步），所以路由上没有任何
守卫或清理需求。

## 2026-08-27 — 新路由 /app/agents/:agentId(Agent Profile)

`agents/:agentId` 挂 [[pages/AgentProfilePage]](懒加载,与其它子页一致),
放在 `teams/:teamId` **之前**只为可读性——两条路径首段不同,不存在 v6 排序
冲突。两个入口写进 `state.from`:Chat 头部身份块(`'chat'`)与 Dashboard
智能体行的身份块(`'dashboard'`),profile 的面包屑据此决定回哪儿。
（当时**没有** `agents/new`；#382 合入后创建 Agent 走 [[ChooseCreateMethodPage.tsx]] 的整页分叉，见上条。）

## 2026-08-19 — /app/account 路由降级为别名

路由本体保留,但语义变了:账户面板住进 Settings 的 ?tab=account pane
(左侧 nav 常驻),本路由只把旧链接带查询参数转发过去
(见 [[pages/AccountPage]]/[[pages/SettingsPage]] 08-19 条)。本文件仅
注释同步,无行为改动。

## 2026-08-06 (3) — /app/account 路由

用户级设置页(account/billing/subscription)懒加载路由;Settings 对
?tab=account 的重定向指向这里。

## 2026-08-06 (2) — /app/teams/new 路由

新增 CreateTeamPage 懒加载路由,列在 teams/:teamId 之前(静态段本就
优先于动态段,列前只为可读性)。

## 2026-08-06 — /app/manage-agents 路由移除

Chat UI v4 把批量管理并入 Dashboard;ManageAgentsPage 的 lazy import 与
路由删除。其余路由结构(element={null} 的 chat/team-chat 技巧、懒加载、
ProtectedRoute)不变。

## 2026-08-12 — ChunkErrorBoundary 包住路由（防部署期白屏，Mark item 10）

路由用 `React.lazy` 切 chunk；发版后老 tab 加载旧 hash chunk→404→未捕获异常→整树卸载成白屏。用 [[ChunkErrorBoundary.tsx]] 包住 `<Suspense><Routes>`。**boundary 是唯一的恢复驱动**（复审后重构：刻意不挂 `vite:preloadError` 全局监听，原因见 [[chunkReload.ts]]）：到达 render 的崩溃**分两类**——stale-chunk 失败 → 一次自愈 + 显示「有新版本，刷新」；真 render bug → 不自愈 + 显示「出错了，刷新重试」（不再把真 bug 伪装成新版本）。另本轮给 `isLoggedIn` 后台预取 `import('@/components/layout/MainLayout')` 补 `.catch(() => {})`（同 [[Sidebar.tsx]] 悬停预取，删监听后预取失败应静默）。

## 2026-08-12 — web-analytics (GTM) init on login

`App()` mounts an effect keyed on `isLoggedIn` calling `initWebAnalytics()`
([[webAnalytics.ts]]). Gated on login on purpose — the loader reads the
per-user opt-out before injecting GTM. Reuses the existing `isLoggedIn`
subscription (added for the expiry banner), not a second one. No-op on desktop,
off the official host, unconfigured, or opted-out.

## 2026-08-10 — workspace-ready after session validation

ProtectedRoute records `workspace_ready` only after the session probe succeeds.
Failed or unreachable sessions do not count as product entry.

## 2026-08-06 (自审补) — ProtectedRoute 不再因 `success:false` 登出

`ProtectedRoute` 的会话校验原本是
`api.getAgents().then(res => { if (!res.success) logout() })`。问题:
`GET /api/auth/agents` 对**处理器里任何未捕获异常**都回 200 +
`{success:false, error}`(见路由末尾的兜底 except)——比如 app 挂载时数据库
抖一下。那不是认证失败,却会销毁整个会话:这正是 401 那条路刚刚被治好的
同一种核弹反射,只是换了触发源,而且**绕过 [[sessionGuard.ts]]**(全程没有
401)。

现在只保留"摸一次后端"的预热语义,判决权在 401 那条路(经 [[api.ts]] 交给
[[sessionGuard.ts]] 确认)。列表拉失败的代价应该是侧栏空着,仅此而已。

**review R2 追加**:调用也从 `getAgents()` 换成新的 `api.getSession()`。前者
为了一个"后端还认我吗"的信号,把用户的全量 agent 列表(外加 active-run 与
预览 enrichment)从数据库里拉一遍再整个丢掉;`/api/auth/session` 不查库,
同样的信号、没有那份开销。

同时把到期横幅的 `isLoggedIn` 从 `getState()` 改成订阅:否则手动登出后,
"Your session expires in 5 hours" 会继续挂在 /login 顶上,直到下一个
10 分钟 tick 才消失。

## 2026-08-06 — auth-expired 记录触发源 + 新增到期预警横幅

**1. 记录触发源。** `narranexus:auth-expired` 的 handler 现在读事件的
`detail: {endpoint, code}`（由 [[sessionGuard.ts]] 附带），登出前打一条
console.warn。2026-08-02 事故复盘时，客户端侧对"是哪个请求把我踢下线的"
一无所知。

**2. 到期预警横幅（琥珀色，可点击忽略）。** JWT 7 天有效且**无续期端点**，
到期前毫无提示，用户是在下一次点击时被当场击毙的。新增一个每 10 分钟 +
`focus` 时检查的 effect：剩余时间进入 `EXPIRY_WARNING_WINDOW_MS`（24h）就
提示，local 模式（无 JWT）恒不显示。解析逻辑在 [[tokenExpiry.ts]]。

横幅与既有 `sessionExpired`（已过期）横幅互斥——已经死了就不必再预告。

## 2026-08-04 — PageFallback 根 h-screen → h-dvh-safe

整屏 loading 占位的高度改用 index.css 的 `.h-dvh-safe`（100vh 兜底 +
100dvh 覆盖），与 MainLayout/WelcomePage 同批收口移动端「100vh 含浏览器
可伸缩 UI」问题。纯样式，无逻辑变化。
## 2026-07-31 — /pay 路由(官网→Stripe 跳板)

新增顶层受保护路由 `/pay` → [[PayPage]](lazy)。放在 ProtectedRoute 里
是设计的一半:未登录访客由既有的 `/login?next=%2Fpay` 机制兜住,付费意图
穿越登录/注册存活——这正是 P0「登录后未跳回付费页」的修复。ProtectedRoute
/ LoginPage 的 next 链路本身没改(验证过三条登录路径都已支持)。

## 2026-07-30 — 删除失效的 402 配额横幅

`quotaExceeded` 横幅（监听 `narranexus:quota-exceeded`）已删。它依赖后端返回
HTTP 402 + `error_code=QUOTA_EXCEEDED_NO_USER_PROVIDER`，而那个触发器在 2026-07-28 免费
额度改成普通 provider 卡时就消失了（运行前配额门禁被移除，resolver 只剩
`NO_PROVIDER_CONFIGURED`，语义是「压根没配」而非「配了但花光」）—— 后端零发出点，横幅
永远不会出现。

它承诺的引导（加自己的 key / 订阅套餐）**没有丢，迁到了失败消息内的按钮**
（[[MessageBubble]] 的 `free_tier_exhausted`），因为新架构下「用完」是运行中错误、不再是
运行前的 HTTP 层事件。

注意另一条死横幅 `freeTierSwitched`（`free_tier_switch` 通知，后端同样零写入点）**本次
未动** —— 它属于「自动切换到自有 provider」那套语义，等 opt-in 自动接续的产品决定。
## 2026-07-23 — Locked Use startup re-assert

`App()` mounts one effect calling `usePowerStore.getState().applyOnStartup()`
— re-asserts a persisted prevent-sleep state after restart (the previous
process's caffeinate assertion died with it). No-op on web / when off.

## 2026-07-20 (续) — 横幅"退出重登"改为"Settings → Account 里接入"

use-subscription 按钮接上后（[[NetmindAccountPanel]] 同日条目），横幅里
"then sign out and back in to link it"的笨拙引导改为指向面板的 Link it now
按钮所在位置。#124 自己就说文案是止血不是终态——终态到了。
## 2026-07-21 — /app/marketplace 路由

新增 lazy MarketplacePage 路由,与其他页面同模式。


## 2026-07-20 — quota-exceeded 横幅文案补「订阅 NetMind.AI 套餐」

与 [[resolver]] / [[failure]] 同批：额度耗尽的用户现在可能已经
被自动绑上了一把没余额的 NetMind key，只提示"添加自己的 API key"对他们无解。

遗留问题（本次未处理，超出改动范围）：**这条横幅是全应用少数没走 i18n 的
用户可见文案** —— App.tsx 至今没有引入 `useTranslation`，为一条字符串把 hook
引进应用外壳风险大于收益。若将来 App.tsx 因别的原因接入 i18n，顺手把它一起收了。

## 2026-07-13 — Agent 实时层熔断器接入

监听 `narranexus:agent-circuit-open`，渲染红色顶部横幅：paused 时带『Resume agent』按钮（调 `api.resetAgentCircuitBreaker` 清暂停），cooling 只提示稍后重试。mirror quota/auth 横幅模式。


## 2026-07-07 — free-tier auto-switch one-time banner (#48)

When the free tier runs out and the backend auto-switches the user to their own
provider, it writes a one-time SYSTEM notice tagged `source.type
"free_tier_switch"`. New effect polls `api.getNotices(true)` on mount + window
`focus` (the switch happens mid-session, server-side, on a request that
otherwise succeeds — no error for api.ts to catch), and on a hit shows a green
dismissible top banner then `markNoticeRead` so it surfaces exactly once. Mirrors
the existing `quota-exceeded` / `session-expired` banner pattern (hard-coded
English copy, click-to-dismiss).

## 2026-06-23 — `/app/you` route added

Added a lazy `/app/you` → [[YouWorkspace]] route under the `/app` (MainLayout)
group. Like `dashboard` / `system`, it is a sub-page rendered through the
`<Outlet>` overlay (so `isSubPage` in [[MainLayout]] is true and it gets the
close-X). Entered from the sidebar user avatar ([[Sidebar]]).

## 2026-06-11 — NetMind ?token= inbound bootstrap; /register removed

Added a one-shot bootstrap useEffect: on app init it calls `takeInboundToken(window.location)` (lib/netmindAuth/tokenInbound) — when the page is opened with `?token=<NetMind loginToken>` (a link from netmind.ai or Arena), it strips the token from the URL immediately and exchanges it for our session via api.netmindLogin, then writes configStore (login + setNetmindToken). `?source=` is stashed in sessionStorage('nx-entry-source') for Phase 2 provisioning. Already-logged-in users are skipped. Also removed the RegisterPage lazy import and the `/register` route — cloud sign-up now links out to NetMind's registration page (see LoginPage). RegisterPage.tsx deleted.

## 2026-06-02 — cloud skips first-login provider setup

`RootRedirect`'s setup gate is now `needsSetup && mode === 'local'` (was
just `needsSetup`). Cloud accounts (`cloud-web` / `cloud-app`) boot on the
system free-tier quota ([[system_service]] / QuotaService), so a
fresh cloud user can chat with zero configuration; the provider screen only
confused users who had no API key to paste. Local installs have no system
provider, so they still must configure one — the gate keeps `/setup` for
`mode === 'local'` only. Cloud users can still add a personal provider later
from Settings (the onboarding checklist + quota panel point them there).

## 2026-05-27 — UpdateBanner mount + updaterStore init

App.tsx now (1) mounts [[UpdateBanner.tsx]] at the root (outside the
router so it surfaces from every page) and (2) calls
`useUpdaterStore.getState().init()` in a useEffect on App mount to
bring the unified auto-updater state mirror online. `teardown()` runs
in the effect cleanup to keep StrictMode happy. Both are no-ops on
web/cloud (the store's `init()` early-returns at `isTauri() === false`,
and the banner only renders on `state.kind === "ready"` which never
happens in browser mode). See [[updaterStore.ts]] for the bridge to
the Rust state machine [[updater.rs]].

## 2026-05-27 — session-expired banner on narranexus:auth-expired

The auth-expired handler now (1) logs out via configStore as before
AND (2) sets `sessionExpired` so a top amber banner explains "Your
session expired. Please sign in again." Auto-dismisses after 12s;
clicking dismisses immediately. Mirrors the `quotaExceeded` banner
pattern at the same site. Why: pre-fix the bounce-to-login was
silent, so cloud users (especially dmg users opening the app a week
after install) saw mysterious errors / abrupt logout with no
explanation. The new wsManager bridge ([[wsManager]]) means WS
AuthError frames also trigger this path, not just REST 401s.

## 2026-05-18 — deep-link receiver (Tauri-only)

New `useEffect` listens for `narranexus://install?url=...&sha256=...`
deep links delivered by the Tauri layer:
1. On mount calls `consumePendingDeepLink()` (Tauri IPC) to drain any
   URL the OS handed the process before React was alive.
2. Subscribes to the `deep-link-received` Tauri event for URLs that
   arrive while the app is already running (forwarded into the live
   instance by `tauri-plugin-single-instance`'s deep-link feature).

URLs are parsed; if the host segment is `install`, navigate to
`/app/templates/install` carrying the same query string — that route
points at `BundleImportPage` which detects URL mode and auto-fetches via
`POST /api/bundle/import/from-url`.

Hook is a no-op outside Tauri (web/cloud build), so `isTauri()` guard at
top.

# App.tsx — Root routing, route guards, and global side-effects

## Why it exists

The entry point for all React Router routing. Defines the complete route tree, implements `ProtectedRoute` and `PublicRoute` guard wrappers, and owns `RootRedirect` — the logic that decides where to send the user on first load. It also mounts the two global side-effect hooks: `useTheme` (dark mode) and `useTimezoneSync`.

## Upstream / Downstream

Rendered by `main.tsx` inside `BrowserRouter` and `QueryClientProvider`. Lazy-imports all page components via `React.lazy` for route-level code splitting.

Reads from `configStore` (`isLoggedIn`, `userId`, `logout`) and `runtimeStore` (`mode`, `setMode`, `initialize`). On `ProtectedRoute`, validates the session by calling `api.getAgents(userId)` — a live check that the JWT is still accepted.

`RootRedirect` checks provider count via a raw `fetch` to `/api/providers` (not through `api.*`) on every root navigation.

## Design decisions

**`ProtectedRoute` checks `!mode` before `!isLoggedIn`.** When the user clicks "Switch Mode", `mode` and `isLoggedIn` are cleared together in a Zustand batch. React Router's navigation to `/mode-select` is enqueued but has lower priority than the render caused by the store update. Without this ordering, `ProtectedRoute` would see `isLoggedIn=false` and redirect to `/login` (with `mode=null`), landing the user on a broken login page with no API URL configured.

**Session validation in `ProtectedRoute` is soft.** If `api.getAgents()` throws (backend unreachable), the user is NOT logged out — they stay in the app. Only a `!res.success` response from a reachable backend triggers logout. This prevents local-mode users from being logged out during a backend restart.

**Hard logout on confirmed session death via `narranexus:auth-expired`.** The `App` component registers a global listener for `narranexus:auth-expired` and calls `configStore.logout()` on receipt. Since 2026-08-06 the event fires only after [[sessionGuard.ts]] has confirmed the session is really dead — not on every 401 (see `request<T>` in `lib/api.ts`). This complements `ProtectedRoute`'s one-shot session check: a JWT that expires mid-session — or is invalidated by a backend restart that recycled session state — gets caught by the next API call instead of leaving the UI to spam silent 401s.

**`RootRedirect` reads `VITE_FORCE_CLOUD`.** Cloud-web deployments set this env var to skip `ModeSelectPage` entirely. On first render with `mode=null` and `VITE_FORCE_CLOUD=true`, `setMode('cloud-web')` is called inline (not in a `useEffect`), which is a Zustand write during render. This is technically unsafe in React strict mode but is a one-time initialization that only fires when `mode` is null.

**All pages are lazy-loaded.** Every `const Foo = lazy(() => import(...))` call creates a code-split chunk. `Suspense` with `PageFallback` shows a spinner while the chunk loads. The only performance trade-off is a ~100ms delay on first navigation to each page.

**`/app/chat` renders `null` as content.** The chat content (`ChatPanel` etc.) is rendered by `MainLayout`'s child slot logic, not by a dedicated route element. The `<Route path="chat" element={null} />` declaration exists only to make the route valid for `Navigate` destinations.

**`/nm-playground` is a public dev-mode route.** Added in M2 (NM design system Phase 1). Renders `NMPlaygroundPage` — a visual gallery of every NM primitive in light + dark side-by-side. No auth required so it can be loaded before login during visual review. Not linked from any navigation. Should ideally be tree-shaken from production builds, but harmless if left in.

## Gotchas

**`initialize()` is called from `RootRedirect` but is a no-op.** See `runtimeStore.ts` — `initialize` was deprecated and is now an empty function. The call in `RootRedirect` is harmless but should be cleaned up once the need to call it is fully gone from all persisted states.

**`PublicRoute` redirects to `/mode-select` if `mode=null`.** A user who navigates directly to `/login` or `/register` with no stored mode (cleared localStorage) will be bounced to `/mode-select`. This is correct but unexpected if the developer clears storage during testing.

**`ProtectedRoute` shows `PageFallback` during session validation.** The `validating` state delays rendering protected content by one async round-trip (`api.getAgents`). On a slow connection this can show the spinner for 1-2 seconds even for logged-in users with valid sessions.

2026-08-19：needsSetup 段注释更新——"onboarding checklist" 改指 balance
panel + 自动供给的引导 Agent（checklist 卡片已退役，纯注释同步，无行为变化）。

## 2026-08-27 — first-run routing

`/setup` (the provider-only first-run page) is now a redirect to `/welcome`, the
one-page first-run flow ([[WelcomePage]]); old bookmarks and docs still work.

The gate itself lives in **`ProtectedRoute`**, not just `RootRedirect` — see
[[onboardingGate]] for why: `/` is one way in, and a `?next=/app/chat` login, a
bookmark or a refresh would each have skipped a flow that only guarded `/`. Both
routes now ask the same cached question ("does this user still owe the welcome
flow?"), and `ProtectedRoute` forwards the intended URL as `?next=` so the flow
can hand the user back to it.

Two routes opt out via `skipWelcomeGate`: `/welcome` (it IS the flow — gating it
would loop) and `/pay` (that route carries a payment intent through login and out
to Stripe; interrupting it with onboarding would lose the checkout).

`RootRedirect`'s old question — "does this LOCAL user have zero providers?" — is
gone. Both modes are eligible for the flow; its own step list adapts, and it
redirects itself out when that list is empty, so no branch here can trap anyone.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
