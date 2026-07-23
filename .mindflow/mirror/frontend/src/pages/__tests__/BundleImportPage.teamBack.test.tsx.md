---
code_file: frontend/src/pages/__tests__/BundleImportPage.teamBack.test.tsx
last_verified: 2026-07-22
stub: false
---

# BundleImportPage.teamBack.test.tsx

黑屏 bug 回归:team 模板安装 deep-link 进入 review 步后,返回按钮必须回到
Marketplace Teams tab(而非空白 deep-link upload 步 = 黑屏,也非 settings)。
mock install-preflight + useNavigate,断言 header 返回导航到
`/app/marketplace?tab=teams`。去掉 exitToOrigin 修复即挂。
