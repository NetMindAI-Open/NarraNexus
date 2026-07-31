---
code_file: frontend/src/components/ui/__tests__/BetaBadge.test.tsx
last_verified: 2026-07-28
stub: false
---

# BetaBadge.test.tsx

钉住两条契约:1) "Beta" 字样是不翻译的品牌字面量,任何语言下都渲染原文;
2) 触发器必须带 `aria-label` 暴露翻译后的预期管理说明(`common.betaTooltip`)
—— Radix tooltip 内容悬停前不挂载,触屏/读屏用户只能靠这条 aria 线。
