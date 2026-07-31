---
code_file: frontend/src/components/settings/NetmindReturnNotice.tsx
last_verified: 2026-07-30
stub: false
---

# NetmindReturnNotice — 付款回跳后的那一行

## 为什么存在

纯展示，配合 [[useNetmindPaymentReturn]]（它负责消费并剥掉 query 参数）。单独成文件
是为了把 [[NetmindAccountPanel]] 压回可读范围，与三个兄弟组件同一套拆法。

## 两个判断

- **渲染在面板的 loading/error 分支之外**。「我的钱到了吗」是用户带着进来的问题，
  不该等面板那几个 fetch，更不该因为其中一个失败就消失。
- **文案只说挣到的部分**。`status=success` 意味着 Stripe 确认付款完成，所以
  「已收到」是实的；但 topup 那条**不能**说「余额已刷新」——NetMind 记账晚一拍，那
  正是 hook 里 settle 重读存在的原因。cancelled 用中性 ink 而非错误色：用户主动退出，
  什么都没坏。

## i18n

`settings.netmind.returnSubscribeSuccess` / `returnTopupSuccess` /
`returnCancelled`，10 语言齐平。`flow` 缺失（旧 session）时落到 subscribe 那条 ——
它不宣称套餐已生效，所以在两种情况下都成立。
