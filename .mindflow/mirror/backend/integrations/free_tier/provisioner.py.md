---
code_file: backend/integrations/free_tier/provisioner.py
last_verified: 2026-07-28
stub: false
---

# provisioner.py — 首次登录时把钱包变成一张普通 provider 卡

## 2026-07-28 — 调度权上交给登录路径

`schedule_ensure_free_tier_provider` 已删。本模块只暴露 `await` 得到的
`ensure_free_tier_provider`；何时跑、和谁排队由 [[auth.py]] 的
`_provision_providers` 决定。原因是它必须**跑在** NetMind provisioner 之前
（钱包是我们确定有余额的凭据），而两边各留一个 fire-and-forget 调度器就
必然并发——那正是新用户 slot 被绑到零余额 Power 卡、agent 一律报
"Claude API error: unknown" 的竞态。

登录侧现在每次登录都调用它（不再只在 `is_new`）：alias `free::{user_id}`
自带幂等，无条件重跑让首次撞上钱包服务故障的用户下次登录自愈。

## 为什么存在

整个免费额度就是这一个函数：开一个 $10 钱包，把钱包 key 当作普通的双行
provider 卡注册进去，绑好 slot。跑完之后这个用户身上**没有任何特殊之处**，
后面所有代码路径都把他当成「配了自己 NetMind 卡的人」来解析。

## 为什么和 netmind_provisioner 长得几乎一样

因为它们本来就是同一件事，只是 key 的来源不同（一个来自用户的 NetMind 账号，
一个来自我们的网关钱包）。刻意保持同构 —— 锁、register/activate 的分叉、
fire-and-forget 包装都一一对应 —— 这样其中一个长出新的边界条件时，另一个的
缺失是肉眼可见的。

## 顺序上不能动的一条

**去重必须发生在调钱包服务之前**，而且钱包服务自身也是幂等的（双保险）。
两次登录竞态绝不能给同一个用户开出两份预算 —— 这是会直接漏钱的。

## activate 的判断

只有在用户「还没有任何可用配置」时才绑 slot。已经配好自己 provider 的用户
不会被劫持 —— 免费卡只是出现在 Settings 里可供切换。

## 一个救不回来的状态

如果钱包已存在但 provider 行不存在（上一次尝试在两步之间崩了），密钥是**取不
回来的**（网关只给一次明文）。此时不能写一张没有 key 的卡（那张卡每次运行都会
401），只能大声报错让人去网关删掉 `free::{user_id}` 让下次登录重开。
