---
code_file: frontend/src/components/settings/__tests__/BrowserManualInstall.test.tsx
last_verified: 2026-09-23
stub: false
---

# 手动安装复制回归

用仅供测试的安装器原文验证引号、空格和换行完整复制；覆盖剪贴板失败后的
可恢复状态、缺少命令时不自行生成，以及命令变化时清除旧的成功提示。

安装、状态检查和取消选择器分别复制安装器提供的原文，并显示真实目录与 shell。
