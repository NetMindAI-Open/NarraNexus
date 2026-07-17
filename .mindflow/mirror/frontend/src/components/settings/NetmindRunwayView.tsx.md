---
code_file: frontend/src/components/settings/NetmindRunwayView.tsx
last_verified: 2026-07-10
stub: false
---

# NetmindRunwayView.tsx — 三池 runway 全景 + 「Free tier first」开关

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
