---
code_file: frontend/src/pages/BundleImportPage.tsx
last_verified: 2026-05-21
stub: false
---

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
