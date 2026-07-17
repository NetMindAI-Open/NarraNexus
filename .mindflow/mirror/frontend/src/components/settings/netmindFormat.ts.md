---
code_file: frontend/src/components/settings/netmindFormat.ts
last_verified: 2026-07-10
stub: false
---

# netmindFormat.ts — Account 面板共享的纯格式化 helper

## 为什么存在

[[NetmindAccountPanel]] 拆出 [[NetmindRunwayView]] / [[NetmindUpsellCard]] 两个
纯展示子组件后,money/百分比/period/日期这几个格式化被多方共用;集中一处避免
copy-paste 漂移,且纯函数可独立单测(12 用例)。

## 各 helper 的语义决策

- `money()`:NetMind 金额字符串可带 4 位小数("9.9300")→ 固定显示 2 位;
  null/空/垃圾 → "—"(绝不显示 NaN)。
- `freeTierPctLeft()`:**取 input/output 两条中更耗尽的一侧**(min)——那才是
  "你还能干多少活"的诚实上限(任一侧耗尽都干不了活)。exhausted → 0;
  feature off / uninitialized / fetch 失败 → null(= 不渲染进度条,而不是 0%)。
  total 为 0 时按"未动过"(ratio 1)处理,防除零。
- `formatPeriod()`:只把 "month" 映射成本地化短标签;**其它值原样透传**——
  dev 环境 Pro 的 period 是 "2day"(types/api.ts:1005 警告),透传让漂移可见
  而不是被藏起来。
- `formatDate()`:Unix 秒 → YYYY-MM-DD;垃圾输入 → "—" 不抛。

## 上下游

消费方:[[NetmindAccountPanel]]、[[NetmindUpsellCard]]。输入形状来自
`QuotaMeResponse` / `SubscriptionPlan`(frontend/src/types/api.ts)。
