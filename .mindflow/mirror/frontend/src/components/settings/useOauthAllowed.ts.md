---
code_file: frontend/src/components/settings/useOauthAllowed.ts
last_verified: 2026-08-28
stub: false
---

# useOauthAllowed.ts — "本调用方可否使用 OAuth 订阅卡"

## 为什么存在

cloud 非 staff 不许加 OAuth 卡(后端 403 + status 路由 `allowed: false`)。
SubscriptionConnect 自己会在 allowed=false 时渲染说明行,但**入口层**
(ProviderSettings 的 Sign-in tab)需要在渲染面板之前就知道答案——tab
指向一个被门禁的面板读作"页面坏了"。独立文件(而非塞在组件里)遵循
同目录 useNetmindPaymentReturn.ts 的先例,也保住 Vite fast-refresh。

## 契约

- `null` = 探测中;`false` **仅**cloud 非 staff;local/cloud-staff 下
  `allowed` 字段是 undefined → 返回 true。消费方判 `=== false`,
  truthiness 会把 local 的入口一起砍掉(P0 反向复发,有测试钉住)。
- 探测失败 **fail-open**(true)——失败不是判决,后端 403 才是边界。
- `enabled` 参数推迟探测:status 路由在 local 真起 `claude auth status`
  子进程,别为关着的 modal 付钱(ProviderSettings 传 `addModalOpen`)。
- 以 `userId` 为依赖:allowed 是 per-user 的(staff 标志)。

## 已知取舍

与 SubscriptionConnect 自己的 status 探测在"modal 开 + Sign-in tab 开"
时会各打一次 claude-status(共两次)——收敛进共享 useProviders/status
store 是已记录的后续项(review 第 4 轮 Minor 1)。
