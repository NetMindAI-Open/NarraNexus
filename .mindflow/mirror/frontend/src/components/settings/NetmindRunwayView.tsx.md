---
code_file: frontend/src/components/settings/NetmindRunwayView.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-20 — 免费额度行值改 token 格式（freeTokensText）

新 prop `freeTokensText`：行值显示"剩余 3.9M / 4.5M tokens"（[[netmindFormat]]
的 freeTierTokensLeft，与条宽同维度），null 时回退旧的百分比串。条宽仍由
freePct 驱动。套餐额度条不变（美元池，百分比语义自洽）。

## 2026-07-18 (同日三改) — 新增套餐额度条（subPct，Pro 溢出模型）

新 prop `subPct: number | null`：Pro 拆分激活时渲染「套餐额度」条（复用免费
额度条同款视觉），**0% 时条保留** + 一行"本周期套餐额度已用完，将于下周期
刷新"（与免费额度的塌缩刻意相反——水箱会回满，塌缩才是错的）。flow 句新增
最高优先分支 `flowProSub`（"先扣套餐额度，再扣你的余额"——拆分激活时免费
额度条已被顶替，不得再提它）。拆分数学在 [[NetmindAccountPanel]]。

## 2026-07-18 (同日二改) — 耗尽后进度条塌缩为一行小字

**事实核查驱动**：免费额度是注册时一次性发放（`init_for_user`，无 cron、无
月度刷新，仅 staff 手动追加）——耗尽后永挂 0% 警示条 + "Used up" 等于修不好
的报警器。改法：panel 侧 `freePct === 0` 时转 `freePct=null`（条消失、扣费
顺序句自动切无免费池变体，#3 单池隐藏规则顺带生效）+ 新 prop
`freeTierExhausted` 渲染一行灰字 `freeTierExhaustedNote`（"免费额度已用完，
用量现从你的余额扣除"）——账单透明度保留、无警示色。`freeTierUsedUp` i18n
键随死分支删除；Row 的 `warn` 参数一并清理。低余额警示/充值引导仍归
action zone 管。

## 2026-07-18 — 「Free tier first」开关整体删除（免费额度优先成为平台行为）

Owner 决策：不再让用户选择用不用免费额度——resolver 恒定先扣免费额度、耗尽
自动落到自有 key（见 [[provider_resolver]] 同日条目）。本组件删掉整个开关段
及 `preferSystem/preferLocked/preferBusy/onTogglePrefer` 四个 props，只剩
纯展示的池子明细（免费额度条 + 赠额行 + 扣费顺序句）。下方"开关锁定规则"
一节自此为**历史记录**（描述已删代码的当年语义），不再对应现行实现。

# NetmindRunwayView.tsx — 三池 runway 全景（原含「Free tier first」开关，已删）

## 为什么存在

重设计的核心 UX 主张:把"免费额度(token 计)/ 月度赠额 / 充值余额(美元计)"
三种钱讲成**一个故事**,替代原来 QuotaPanel(token 卡)+ 余额 hero(美元卡)的
割裂呈现。纯展示组件:数据与 toggle handler 全部由 [[NetmindAccountPanel]] 注入,
自身零 fetch、零业务逻辑——便于面板测试直接断言文案。

## 2026-07-11 — 余额移出为 hero;赠额标"已计入";单池隐藏 flow

顶部重排后:**余额不再在此**(挪到面板的 balance hero,因为 `free_credit` 是
赠额+充值合并的可花总额,是全卡锚点)。本组件只剩"池子明细":免费额度条 +
(Pro)赠额行 + 扣费顺序 + prefer 开关。赠额行改成"本月赠额 (已计入余额) $X/月"
—— **不是可加数字**(接口把赠额并进了 hero,单独再列一份会让用户以为能加起来花)。
`showFlow = freePct!==null || grantText`:**单池(仅余额)时隐藏扣费顺序句**(#3,
"你花钱会扣钱"是废话);2+ 池才显示。flowFreeNoTier key 随之删除。

## 结构

免费额度行+进度条(`freePct===null` 时整行不渲染;0 显示 "Used up" 警示色)→
赠额行(`grantText` 仅 Pro 注入)→ 余额行 → 扣费顺序一句话 → 分隔线 →
「Free tier first」switch。

扣费顺序文案按 `freePct !== null` × `flowIsPro` 四选一(flowFree/flowPro/
flowFreeNoTier/flowProNoTier)——**屏幕上没有免费额度条时绝不说"先扣免费
额度"**(UI review:本地 quota 关闭时那句话在描述一个用户看不见的池子)。

## 开关(原 QuotaPanel prefer_system)的锁定规则

`disabled = preferLocked && !preferSystem`——免费额度 exhausted 时**只锁 ON
方向**(开需要额度),OFF 永远允许(切走用自己的额度)。这是后端
`set_preference` "OFF is always allowed" 409 守卫的 UI 镜像;历史教训:曾写成
`disabled={exhausted}` 把已开启的用户困死在 402 循环(见旧 QuotaPanel #48 注释,
该组件 2026-07-10 已删,教训搬到这里)。`preferSystem===null` 时整个开关区
不渲染(quota feature off / uninitialized)。

## 坑

- 进度条填充色:exhausted 用 `--color-warning` 而非 error——用完免费额度是
  预期生命周期事件,不是错误。进度条带 `role="progressbar"` + aria-valuenow
  (review 修复:纯视觉 div 对屏幕阅读器不可见)。
- switch 是自绘 `role="switch"` button(项目无现成 Switch ui 组件);测试用
  `getByRole('switch')` 定位。`disabled = preferBusy || (preferLocked &&
  !preferSystem)` —— preferBusy 是面板注入的在途标记(review 修复:防双击
  并发让开关落在与用户最后意图相反的状态)。
