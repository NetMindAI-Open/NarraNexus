---
code_file: frontend/src/components/ui/FeedbackDialog.tsx
last_verified: 2026-07-10
stub: false
---

## 2026-07-10 (3) — 修布局：必须包 DialogContent / DialogFooter

初版把 children 直接塞进 `Dialog`,而 `Dialog` 的 body **零 padding**（`p-5`
在 `DialogContent` 里,`ConfirmDialog` 是范例）,导致 `w-full` 的 textarea 一直
顶到弹窗边缘,视觉上"输入框=整个弹窗"（Owner 2026-07-10 指出）。现在按惯例
DialogContent（内容）+ DialogFooter（按钮,带上边框）,textarea rows=4 +
resize-none,字段共用 FIELD_CLASS。已加两个回归测试钉住这个结构。


## 2026-07-10 (2) — 打开入口变更

打开方从 Sidebar footer 改为右下角浮动 [[FeedbackButton.tsx]]；弹窗本身不变。


# FeedbackDialog.tsx — 手动反馈弹窗

## Why it exists

Feedback 机制的显式兜底通道（自动通道是 Agent 的 submit_feedback 工具）。
类别下拉 + 500 字文本框，经 api.submitFeedback 走后端中继。

## 设计决策

- 提交失败也显示感谢页——反馈 UX 绝不能对用户报错（api.submitFeedback 的
  异常被就地吞掉）。
- 类别枚举与服务端 CATEGORIES 对齐，但不含 repeated_failure（那是 Agent
  自察专用类别，用户视角无意义）。
