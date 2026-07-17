---
code_file: frontend/src/components/settings/NetmindTopUpControls.tsx
last_verified: 2026-07-11
stub: false
---

# NetmindTopUpControls.tsx — 充值("Add credits")控件

## 2026-07-11 — rechargeDesc 措辞:强调"一次性、无需订阅"

`rechargeDesc` 内联默认从"Top up your … balance. Credits are kept…"改为
"One-time top-up, no subscription. …"——在管理弹窗里 Pro 卡和充值并列时,这句
点明充值相对订阅的差异(一次性、不绑订阅)。纯文案,渲染/行为不变。

## 为什么存在

从 [[NetmindAccountPanel]] 抽出的纯展示组件——直接动因是主面板重设计后超过
800 行铁律(853 行),充值 JSX 块(~95 行)是内聚度最高的抽取对象。抽取后
DOM 与文案完全不变,面板测试零改动。

## 职责边界(重要)

**只管渲染**:档位按钮($5/$10/$20/$50,`RECHARGE_TIERS` 常量随迁至此)+
自定义金额输入 + Recharge 按钮 + processing/success/failed 反馈行(含
"Stop waiting" 逃生口)。**不管**:同步双击守卫(rechargeRef)、poll
generation(rechargeGenRef)、Stripe kickoff、金额校验——这些涉及钱的守卫
逻辑全部留在 [[NetmindAccountPanel]] 的 handler 里,通过 props 注入。改充值
行为去面板,改充值长相来这里。

## 上下游

- 消费方:仅 [[NetmindAccountPanel]](`topUp` 变量,三处复用:free 的 manage
  展开区 / free×low 的链接展开 / pro×low 直出)。
- `RechargeState` 类型随迁至此并 export,面板 import 回去。
