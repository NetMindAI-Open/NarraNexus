---
code_file: frontend/src/components/settings/netmindRunway.ts
last_verified: 2026-07-10
stub: false
---

# netmindRunway.ts — Account 面板的 runway 健康度分类器

## 为什么存在

[[NetmindAccountPanel]] 的 UI 由 plan × runway 两个正交维度驱动;本文件独占
runway 这一维。`deriveRunway(quota, fee) → 'healthy' | 'low'` 决定面板是"安静
放心"(不显示任何花钱按钮)还是"升起一个情境化动作"(free→upsell Pro,
pro→充值)。**upsell 何时出现的唯一真源在这里**——改推销时机只改这一个函数。

抽成独立纯函数是为了不挂载组件就能单测(8 用例覆盖全分支)。

## 分类规则(与实现一一对应)

1. 免费额度 `enabled && status==='active'` → 恒 **healthy**(欠费/不合格只挡
   付费用量,免费额度还能载着用户跑,所以只作 warning 不强推动作)。
2. 否则 `has_arrears` 或 `eligible===false` → **low**(硬阻断)。
3. 否则余额缓冲决定:`Number(free_credit) >= LOW_BALANCE_USD` → healthy;
   NaN(畸形字符串)/不足 → **low**。故意 fail-toward-action:宁可多推一次,
   不可少提醒一次。

## 上下游

- 输入:`GET /api/quota/me`(无信封 union,backend/routes/quota.py 权威)+
  `GET /api/billing/fee-info`(NetMind verbatim 代理,字段全可选)。
- 消费方:仅 [[NetmindAccountPanel]]。

## 坑 / 待定

- **`LOW_BALANCE_USD = 1.0` 未经产品确认**,集中常量一处、注释已标注;改阈值
  只动一行。
- `Number()` 而非 `parseFloat`:"5abc" → NaN → low,绝不静默多算余额。
- fee 全挂(fetch 失败)时 quota 非 active 会判 low → 上游故障时可能误显
  upsell。当前接受(fail-toward-action);要改成 fail-quiet 在第 2/3 步之间加
  fee==null 早退并补单测。
