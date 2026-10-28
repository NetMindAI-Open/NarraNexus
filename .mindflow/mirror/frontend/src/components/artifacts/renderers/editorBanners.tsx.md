---
code_file: frontend/src/components/artifacts/renderers/editorBanners.tsx
last_verified: 2026-08-20
stub: false
---

# editorBanners.tsx — 编辑面共享横幅

409 冲突二选(保留我的/加载对方的)+ 草稿恢复提示。ResidentTextEditor
与 MarkdownRenderer 共用——两种 kind 的措辞不许漂移。文案键
artifacts.editor.*(10 语言)。

## 2026-08-20 — 横幅枚举更新为三条(#334 r2 C3)

409 冲突二选 + 草稿恢复 + **DraftUnavailableBanner**(草稿层容不下
此文件,红横幅催保存)。枚举式 md,加横幅必须来改这里。
