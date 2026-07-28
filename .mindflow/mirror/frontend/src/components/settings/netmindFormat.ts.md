---
code_file: frontend/src/components/settings/netmindFormat.ts
last_verified: 2026-07-28
stub: false
---

## 2026-07-28（补）— 免费额度专用 `creditMoney`，6 位小数

Owner 现场反馈：账号明明用过了，免费额度还是 `10`。数据没错（接口返回
`remaining: 9.993714`），是 `money()` 的 `toFixed(2)` 把它抹平了 —— 免费额度
上跑一轮真实对话只花几厘（~$0.0027），两位小数下**一整段使用都四舍五入没了**，
看起来像根本没扣。

新增 `creditMoney`（`toFixed(6)`，网关自己的精度）只给免费额度那一行用；
`money()` 保持两位 —— 余额 hero 和套餐额度不是亚分级数字，六位只会变难读。
补零是有意的：位数固定，轮询刷新时数字不会左右跳。

## 2026-07-28 — 免费额度的单位从 token 变美元

`freeTierTokensLeft` / `formatTokens` 换成 `freeTierCreditLeft`，
`freeTierPctLeft` 从「input/output 里更枯竭的那一维」改成「钱包已花掉多少
比例」。

双维度的写法本来就是 token 计量的产物（input/output 差价约 5 倍，所以必须
取更紧的那一维）。钱包是单一美元数，天然只有一个维度 —— 而且这样一来，
这一行的数字和它下面那个余额 hero 说的是同一件事。

## 2026-07-20 — 新增 formatTokens + freeTierTokensLeft（行值从 % 改 token）

Owner 走查："剩余 87%" 看不出一个百分点值多少，行值改为
"剩余 3.9M / 4.5M tokens"。`freeTierTokensLeft` 返回**与百分比条同一
（更耗的）维度**的 remaining/total——数字和条永不打架；`formatTokens`
紧凑格式（4.5M / 900K / 850，一位小数去尾零）。百分比 `freeTierPctLeft`
保留：仍驱动条宽，且是 token 串缺失时的行值兜底。

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
