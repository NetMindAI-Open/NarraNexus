---
code_file: frontend/src/components/icons/ModelBrandIcons.tsx
last_verified: 2026-08-27
stub: false
---

# ModelBrandIcons.tsx — LLM 厂商的真实品牌标志

## 为什么存在

Dashboard 智能体目录的 Framework / Model 两列要能一眼看出「这个 agent 跑在谁家
模型上」。八个厂商（Claude / OpenAI / Gemini / GLM(Zhipu) / Kimi(Moonshot) /
Qwen / MiniMax / DeepSeek）的官方 monochrome 路径，做法同
[[ChannelBrandIcons.tsx]]：从 Simple Icons（CC0）取，本地建组件，不加依赖。

## 上下游关系

- **调用方**：[[../../pages/DashboardPage.tsx]]（`FRAMEWORK_BRAND_ICONS` 直接映射
  framework id；Model 列走 `getModelBrandIcon` 按 model id 猜厂商）、
  [[../../pages/AgentProfilePage.tsx]]。
- **匹配逻辑不在这里**：见下一节。

## 只放组件，匹配逻辑在 lib/

本文件**只导出 React 组件**（8 个图标 + `ProtocolIconComponent` 类型），不导出
任何普通函数——`protocol` / `modelId` → 图标组件的匹配逻辑在
[[../../lib/modelBrandIcons.ts]]。这是被 ESLint 逼的：react-refresh 插件的
`only-export-components` 规则不允许一个文件混合导出组件和普通函数。

## 品牌真彩，不是 currentColor

`BrandIcon` 有必填 `color` prop，每个组件传自己的官方 hex：Claude `#D97757`、
DeepSeek `#5786FE`、Gemini `#8E75B2`、Qwen `#6950EF`、MiniMax `#E73562` 都是
Simple Icons 注册表里现成的值；OpenAI 和 Kimi 都是 `#000000`（前者官方 style
guide 自己说的黑白无强调色，后者 Simple Icons 条目本身就是黑）；Zhipu `#3859FF`
来自 Iconify 的 `thesvg-color`。

## Zhipu 的 fillRule 坑

Zhipu 不在 Simple Icons 目录里，是从 Iconify 的 `thesvg` 集合抓的，它的 path 用了
`fill-rule="evenodd"`（复杂复合路径，不带这个属性会把镂空处填死）。所以共享的
`BrandIcon` 多了个 `pathFillRule` 参数——**命名成 `pathFillRule` 而不是
`fillRule`**：顶层 `<svg>` 自己也认识 `fillRule` 且其类型允许 `'inherit'`，跟这里
要传给 `<path>` 的更窄的 `'evenodd' | 'nonzero'` 撞类型，直接叫 `fillRule` 会跟
展开的 `...props` 冲突报 TS2322。

## Gotcha

**OpenAI 的黑标在暗色主题下会糊掉**。它是纯 `#000000`，暗背景上几乎不可见。
调用方必须自己补救——[[../../pages/DashboardPage.tsx]] 的做法是在图标是
`OpenAIBrandIcon` 时加 `dark:invert`。新增调用方时别忘了这条，否则 Codex 框架的
agent 在暗色主题下看起来「没有图标」。
