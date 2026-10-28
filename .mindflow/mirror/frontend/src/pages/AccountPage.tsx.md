---
code_file: frontend/src/pages/AccountPage.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19(二)— 别名测试钉住

AccountPage.alias.test:查询参数保留、`tab` 覆盖不重复。

## 2026-08-19 — 退化为路由别名

账户面板搬进 Settings 的 account pane(左侧标签栏保留,见
[[SettingsPage]])。本路由只为旧链接/书签存活:带全量查询参数
Navigate 到 /app/settings?tab=account。页面自己的 header/hint 删除。

## 2026-08-19 — 内容列居中

`max-w-3xl` 容器加 `mx-auto`,与 [[SettingsPage]] 同批(Owner:设置类
页面内容要居中,不贴左)。

# AccountPage — 用户级设置(account / billing / subscription)

## 为什么存在

Chat UI v4 迭代:Owner 要求把「用户的钱与身份」从 app Settings 里拆出来,
挂在侧栏底部账户弹层下(用户级 ≠ 应用级)。内容 = 既有
NetmindAccountPanel 原样(免费额度 / Power 余额 / 订阅 / 充值一张卡),
面板内部零改动。

## 关键点

- **Stripe 回跳**:后端仍返回 /app/settings?tab=account&status=…;
  SettingsPage 首渲染检测到 tab=account 即整串 query 转发到本页,
  面板内的 payment-return 处理(useNetmindPaymentReturn)照常读参。
- 非 Power 会话(无 netmindToken)直达 URL 时显示提示文案,不留白页
  (面板自身 self-gate 为 null)。
