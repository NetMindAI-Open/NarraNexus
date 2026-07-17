---
code_file: frontend/src/components/ui/__tests__/FeedbackButton.test.tsx
last_verified: 2026-07-10
stub: false
---

# FeedbackButton.test.tsx

钉住浮动入口契约：点击开弹窗；`aboveHelp` 为真时 `bottom-14`（让开 "?"）,
为假时 `bottom-4`（自己占角位）——后者正是子页面路径,PR#88 review 指出的
可达性回归就在这条线上。
