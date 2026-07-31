---
code_file: frontend/src/components/artifacts/renderers/UrlRenderer.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
切换嵌入模式失败时只表现为「模式没变」。顺带注册了 `artifacts.url.toggleFailed` —— 它此前只有代码里的内联兜底、en.json 未注册，于是非英语用户一律看到英文。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

**chrome 不在调用点重复**：标题 / OK 文案 / danger 由 `useNotice` 提供，调用点只写
message。第一版把这三行在 6 个文件里复制了 9 遍（评审点名），改文案要改 9 处。这同时把
`useConfirm` 默认值 `'Notice'` / `'OK'` 硬编码英文、不走 i18n 的洞补在一处 ——
不必去动那个 20+ 调用方共用的原语。共享 key
`common.{noticeTitle,doneTitle,actionFailedTitle,ok}`，10 语言。

`notifyDone` 与 `notifyPending` 是分开的：`noticeTitle` 的 10 个译法都是「请稍候」语义
（稍等一下 / 少しお待ちください / Одну секунду），拿它当成功提示的标题会让用户以为还在
进行中 —— 所以成功走 `doneTitle`。

用一条**仓库级静态契约测试**钉住（`lib/__tests__/no-native-dialogs.test.ts`）：扫描全部
源文件，禁止任何 `window.alert/confirm/prompt` 调用。这类 bug 前两轮都是靠人读代码发现的
—— 单元测试反而 stub 掉了 `window.confirm` 因而什么都没证明。grep 是唯一能覆盖「还没被
写出来的文件」的断言。

# UrlRenderer.tsx — renderer for URL-tab artifacts (application/x-url)

## Why it exists

Renders an `application/x-url` artifact. The entry file is a JSON doc
(`UrlArtifactDoc`) holding the URL + the server-side embed verdict; this
renderer fetches it through the token-authed raw route, then:

- `effective_mode === 'iframe'` → iframes the EXTERNAL url directly.
- `effective_mode === 'stream'` → fallback card (open-in-new-window). This is
  the seam where the future streaming renderer (方案三) plugs in.
- always shows a mode toggle so the user can override a wrong verdict; the
  override persists via `artifactsApi.setEmbedMode` and a `refreshKey` bump
  re-mints the token + refetches the doc.

## Design decisions

- The iframe `src` is the external URL, not our backend, so HtmlRenderer's
  Tauri mixed-content blob dodge is unnecessary for https targets — one code
  path serves both run modes (铁律 #7).
- iframe sandbox is `allow-scripts allow-same-origin allow-forms allow-popups
  allow-popups-to-escape-sandbox` — a real third-party site needs its own
  origin/storage/forms to function. `allow-same-origin` is safe ONLY because a
  URL tab is cross-origin third-party content; a tab pointing at our OWN origin
  would become a same-origin scriptable iframe reaching the app token, so the
  backend (`url_artifact._reject_self_origin`) refuses to open one. The sandbox
  safety DEPENDS on that guard — don't copy it to a same-origin renderer.
- Navigation (2026-07-22, after a revert): a cross-origin page's link clicks
  are invisible to us (same-origin policy) — we can neither read the target
  URL nor redirect it into a new in-app tab. A `target="_blank"` link has only
  two possible fates: open a new OS-browser tab, or be blocked. The sandbox
  KEEPS `allow-popups allow-popups-to-escape-sandbox` so such links WORK (open
  in the browser) — a dead link is worse than one that opens externally. (An
  earlier same-day change dropped the popup flags to stop the "jump out", but
  that blocked target=_blank links entirely — reverted.) Same-frame links
  navigate in the tab. True "every link opens as an in-app tab" is a
  streaming-browser capability, not an iframe one. `allow-top-navigation` is
  NEVER granted (an embedded page must not be able to navigate our whole app).
  The mode toggle labels are "Inline" (iframe) / "External" (the
  open-in-browser fallback card, formerly the misleading "Full").
- RUN-MODE (铁律 #7): the popup/open-in-browser behavior above holds in
  BROWSER mode only. On the packaged Tauri DMG, WKWebView blocks popups (see
  netmind_oauth.rs), so URL-tab target=_blank links are likely STILL dead on
  desktop until a Tauri new-window handler routes them to the OS browser (or
  the streaming browser lands). Follow-up tracked (author-local todo).
- The doc fetch uses `fetchArtifactText` like the other text renderers
  (Csv/Markdown/Chart) — same pattern, same Tauri behavior.

## Gotcha

`effectiveEmbedMode` (in types/artifact.ts) collapses recommend + override;
don't read `recommended` directly in the renderer or a user override is
ignored.
