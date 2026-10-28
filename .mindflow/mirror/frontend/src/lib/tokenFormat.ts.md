---
code_file: frontend/src/lib/tokenFormat.ts
last_verified: 2026-08-28
stub: false
---

## 2026-08-28 — 最后一份副本收编

文件头上那条「InnerThoughtCard 仍有自己的 `formatTokens`，另行跟踪」的 NOTE
可以销了。为 Conversation 视图抽 [[RunStatChips]] 时必须二选一，本文件的规则
胜出，那张卡片的私有副本删除。至此"一个 token 数怎么显示"在用量界面上只有
一处答案。

# tokenFormat.ts — 用量界面的统一显示规则（求和 / 格式化 / 模型标签）

## 为什么存在

2026-08-19 账户页新增「NarraNexus 用量」区（[[NarraUsageSection.tsx]]）时，需要的
函数 [[CostPopover.tsx]] 里已经有了。**两份独立的实现会漂，而且是静默地漂** —— 同一
周的用量在一个屏幕读 1.2M、另一个读 1.23M，读者无从判断哪个被四舍五入过。于是收进
这里共用。

文件里住着三类东西，**求和那类比格式化那类重要得多**：格式化错了是难看，求和错了是
数字差一个数量级，而且已经真的发生过一次。

## 一、求和：三个输入桶必须一起加

`inputSideTokens` / `totalTokens`（逐模型、逐日的形状）与
`summaryInputSideTokens` / `summaryTotalTokens`（`total_` 前缀的 CostSummary 形状）。

`input_tokens` **只是全价桶**；cache read（0.1x）与 cache write（1.25x）是独立列，
在一个 cache 命中良好的 run 上占输入侧 >99%。只加第一个，就会出现 2026-07-30 那次
事故的形状：一周 1.2M token 的 agent 显示 “input 213”，而且 helper 被排到主循环前面
（详见 [[CostPopover.tsx]] 2026-07-30 条目——**那次事故的正本在那边**）。

`?? 0` 是承重的：前端热更了、后端还没重启时，旧响应没有这些字段，`undefined` 进求和
渲染出 `NaN`（同日实测撞到过）。

**为什么是四个显式导出，而不是一个靠 `in` 判别形状的聪明函数**：第一版就是
`bucketTotal(d: CostModelBreakdown | CostSummary)` 用 `'total_input_tokens' in d`
分叉。加入第三种形状（`CostDailyEntry`）时这个判别式就不够用了，而它失败的方式是
**悄悄走错分支**，不是编译报错。前缀不同就分开导出。

调用方（改契约时这四处要一起看）：
[[NarraUsageSection.tsx]]、[[CostPopover.tsx]]（总计 / 逐模型 / 逐日三处）、
[[InnerThoughtCard.tsx]]（只用输入侧那个）。

## 二、`shortModelName`：`by_model` 的 key 不是模型 id

**这条规则的正本在这里**，另两处（[[NarraUsageSection.tsx]]、[[CostPopover.tsx]]）
只指过来，不各写一遍。

`GET /api/agents/{id}/costs` 的 `by_model` **不按模型 id 分桶**，它把每一行按
`call_type` 折成恰好两个合成 key（[[agents/cost.py]]）：`agent_loop` →
`__main_model__`，其余 → `__helper_model__`。照抄 key 渲染，就是把裸的
`__main_model__` 打到用户屏幕上 —— 2026-08-19 真机验证前，这已经发生了一次。

**label 由调用方传入**（`{ main, helper }`），不在这里读 i18n：`lib/` 不该 import
i18n。两个调用方**复用同两个 key**（`cost.popover.modelUsage` /
`cost.popover.helperUsage`）—— 同一个桶不能在两个界面有两个名字。

去日期后缀那段对这个端点**今天是不可达的**，留着是给任何拿到真实模型 id 的调用方的
合理缺省（有一条「契约变宽」的测试钉着）。

## 三、格式化

`formatTokens`（`<1000` 原样 / `<1M` 一位小数 k / 否则两位小数 M）与 `formatCost`。
`formatCost` 的 `<$0.0001` 兜底推理见 [[CostPopover.tsx]] 2026-08-03 条目 —— 同样
**不在这里重述**，免得两处慢慢说不同的话。

## Gotcha：`formatTokens` 还有第二份

[[InnerThoughtCard.tsx]] 自带一份 `formatTokens`，M 档是**一位**小数（本文件两位）。
它已经改用本文件的 `inputSideTokens`（求和这条规则统一了），但格式化那份没合 ——
合并会改变那张卡的渲染和它的测试断言，属于另一件事，记在
`reference/self_notebook/todo/2026-08-19-third-token-formatter-in-inner-thought-card.md`。
