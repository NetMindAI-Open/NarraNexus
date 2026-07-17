---
code_file: frontend/src/components/ui/FeedbackButton.tsx
last_verified: 2026-07-10
stub: false
---

# FeedbackButton.tsx — 右下角浮动反馈入口（桌面端）

## Why it exists

Owner 定的位置：反馈按钮浮在右下角帮助 "?"（[[HelpButton.tsx]]）正上方，
沿用其圆形 + nm-card 视觉语言，右下角形成"求助/反馈"角。持有
[[FeedbackDialog.tsx]] 的开关状态。

## 设计决策

- **`aboveHelp` prop 而非写死 offset**：HelpButton 只在聊天页渲染（它讲的是
  聊天页引导）。子页面（settings/system/dashboard）没有 "?"，此时反馈按钮
  下沉占据 `bottom-4` 那个角位；聊天页才上移到 `bottom-14`。
- **挂在 [[MainLayout.tsx]] 顶层,不在 ChatView 内**：它取代的 #86 sidebar
  footer 入口是全站可见的,挂进 ChatView 会让子页面丢失入口（PR#88 review
  抓到）。
- **移动端不用本组件**：右下角属于 composer（与 HelpButton 排除移动端同因），
  移动端入口在 [[Sidebar.tsx]] drawer footer。两者互斥,每个视口恰好一个入口。
