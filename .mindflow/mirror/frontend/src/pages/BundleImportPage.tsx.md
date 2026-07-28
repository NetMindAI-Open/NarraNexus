---
code_file: frontend/src/pages/BundleImportPage.tsx
last_verified: 2026-07-23
stub: false
---

## 2026-07-23 — done 屏列出 warnings 正文(review #3)

DonePanel 原来只在汇总里显示"N warnings"计数,用户看不到具体哪条(比如某 agent
的 name/description 被导入修剪)。改为在汇总框下新增一个黄色 warnings 区块,把
`result.warnings` 每条正文用 `<li>` 列出(复用 review 阶段同样的渲染形态)。
新增 i18n `pages.bundleImport.done.warningsTitle`。

## 2026-07-22 — deep-link 返回修复(黑屏 bug)

deep-link 模式(`?url=` / `?teamTemplate=`)下没有真正的"上传第一步",原先
review 的返回按钮 `setStep('upload')` 会渲染空白 upload 面板(preflight 成功后
既不 busy 也无 error)= 黑屏。新增 `exitToOrigin`:teamTemplate 回
`/app/marketplace?tab=teams`,否则回 settings。header 箭头、review 返回
(仅 deepLinkMode)、done 关闭统一走它;非 deep-link 的 review 返回仍是
setStep('upload')(有真实上传步,不回归)。


## 2026-07-21 — ?teamTemplate= deep-link(Team Marketplace)

新增第二种 deep-link:`?teamTemplate=<id>` 调 install-preflight(服务端从我们
store resolve+验 sha256+本地 importer preflight)→ 直接进 review 步。与
`?url=` 平行,共用 deepLinkMode 渲染分支;review/confirm UI 零改动复用。


## 2026-07-13 — credential clashes + activation + LLM-config guidance

Review step surfaces `credential_clashes` (bot already bound here → will be skipped). Done panel shows imported/skipped credential counts, a 'configure the LLM before use' callout (imported agents carry no provider config → incompatible default → silent channel failures), and a 'go activate imported channels' hint.

## 2026-05-21 — marks onboarding `template_applied`

On a successful import confirm (`runConfirm`), fires
`api.markOnboardingStep(userId, 'template_applied')` — best-effort,
swallowed on failure so it can never surface as an import error. This is
what checks off the "start from a template" row in the onboarding
checklist. A confirmed import is the completion signal; merely opening
the templates marketplace is not.

# BundleImportPage.tsx — Import wizard (subproject 2)

3 步：Upload (drag-drop) → Review (preflight 结果) → Done。

## URL 模式（2026-05-18 — 一键安装入口）

`?url=<bundle-url>` 进来时切到 **url-mode**：第一步不是用户上传，而是
`importBundleFromUrl(urlMode, expectedSha256)` 让后端自己抓取（SSRF 白名单
保护），第一步标题/标签从 "Upload" 变成 "Fetch"、整页标题从 "Import bundle"
变成 "Install template"。Review / Done 两步与上传模式完全共用。

抓取自带 **auto-retry + 手动 Retry**：网络抖动或后端刚启动时自动重试；
重试成功后清掉 "waiting for backend" 横幅。这是 narra.nexus 一键安装链路
（经 LoginPage 的 `?next=` 穿过登录墙后）落到的页面。

## Review panel 展示

- Bundle 元数据（format/version/exported_at/sha256）
- Will create 列表（agents / team / skills / mcp_hints）
- Name clashes（含 `(N)` 后缀提示）
- Warnings（manifest.warnings + 系统级）
- Embedding compatibility（advice 文案，不阻断）
- 如果 manifest.team.intro_md 有内容，渲染成 `<pre>` 框

## Done panel

显示 import 摘要 toast + mcp_hints_data 列表（用户决定是否手动加 MCP）。

`onViewIntro` 跳 `/app/chat`（B10：还没真做 team detail page，临时跳到 chat）。
