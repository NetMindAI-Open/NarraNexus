---
code_file: frontend/src/pages/BundleImportPage.tsx
last_verified: 2026-05-08
stub: false
---

# BundleImportPage.tsx — Import wizard (subproject 2)

3 步：Upload (drag-drop) → Review (preflight 结果) → Done。

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
