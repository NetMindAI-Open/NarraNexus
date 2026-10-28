---
code_file: frontend/src/components/artifacts/renderers/__tests__/browserInput.test.ts
last_verified: 2026-09-23
stub: false
---

# 远端输入坐标契约

将画布的显示框与实际图像分开测试，确保 object-contain 留白不会被误算成网页坐标。
修饰键位遵循 CDP 编码，组件无需复制映射逻辑。纯坐标测试不替代真实浏览器的输入验证。
