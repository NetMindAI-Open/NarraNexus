---
code_file: frontend/src/components/settings/NetmindActionZone.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 (续) — pro 管理弹窗顶部加已订阅套餐卡

"管理订阅与余额"弹窗内容顺序变为：[[NetmindUpsellCard]]（`subscribed` 模式，
套餐介绍 + ✓ 已订阅徽章，无 CTA）→ 取消订阅行 → 充值 → 定价链接。分支表中
`pro_active × healthy` 的弹窗描述相应更新。

## 2026-07-18 — 定价链接换官网 + 补齐两个 pro 弹窗

`PRICING_URL` 从 `www.netmind.ai/pricing` 换成
`https://website.narra.nexus/pricing`（用产品语言讲套餐的官网定价页，Owner
指定）。原来只有 free 升级流程和 pro×low 带定价外链，**pro_active×healthy
的"管理订阅与余额"弹窗和 pro_cancelled 的"管理余额"弹窗漏了**——已补
`{pricingLink}`。Owner 问过要不要常驻主面板：定案**不放**，链接只出现在
决策时刻（弹窗/low 态），维持"健康时安静"的面板哲学；补齐后所有花钱决策
场景均有覆盖。

# NetmindActionZone.tsx — plan × runway 动作区(至多一个主 CTA)

## 为什么存在

code review 抽取:原 `actionZone()` 是 [[NetmindAccountPanel]] 里 ~100 行的内联
闭包(超 50 行函数守则),且面板当时贴着 800 行上限(784)。抽出后面板降至
~665 行,分支逻辑成为可独立阅读的组件。纯展示:所有带守卫的资金 handler
(subscribe/cancel/reactivate)与 `showManage` 状态仍由面板持有,经 props 注入;
`topUp` 以 ReactNode 传入(其状态守卫在面板)。

## 分支表(与实现一一对应)

| 条件 | 呈现 |
|---|---|
| pro_cancelled(无视 runway) | Resume 主按钮 + Manage balance › |
| free × low | 警示文案 + [[NetmindUpsellCard]] 主推 + 充值降级链接 + 定价外链 |
| pro_active × low | needTopup 文案 + 充值直出(已是 Pro,不再 upsell) |
| free × healthy | Manage plan & credits › **开 Dialog 弹窗**(Pro 卡主推 + "只想临时充值? 添加余额 ›" 点开才显充值档;非并排两按钮) |
| pro_active × healthy | 会员价生效提示 + Manage ›(展开才有 Cancel + 充值) |

## 文案门控(review 修复项)

free × low 的警示句按 `freeTierExhausted`(freePct===0,**确知**耗尽)选
`exhaustedChoose`("Free tier used up…");quota 状态未知/禁用时用中性的
`lowChoose`("You're running low…")——**绝不断言未观察到的事实**。

## 管理改弹窗 + 组件自持状态(2026-07-11,Owner 定案)

- healthy 态的"管理套餐与余额"从**内联折叠**改成**点击开 Dialog 弹窗**(复用
  LLM Providers 加服务商用的 `@/components/ui` `Dialog`/`DialogContent`,size=lg)。
- 弹窗内**不给"订阅 Pro / 充值"二选一**:free → `planBlock`(Pro 价值卡主推 +
  "只想临时充值? 添加余额 ›" 点开才露充值档 + 定价外链);pro_active → 取消订阅 +
  充值;pro_cancelled → 仅充值。
- 组件**自持** `manageOpen` / `showTopUp` 两个纯 UI 开关(从面板收回,面板不再传
  showManage/onToggleManage)。关弹窗时 reset showTopUp。
- `planBlock` 同时被 **free×low 内联视图**和**free healthy 弹窗**复用(低额是紧迫
  态,内联;充足是平静态,藏弹窗)——两者内容一致,只差顶部"余额不足"警示句。
- Model Defaults 修正:连接行"切换驱动服务商"指向 **Model Defaults**(非 LLM
  Providers)——后者已是纯凭证钱包,选驱动 provider 在 Model Defaults 写 slots。

## Owner 走查修复(2026-07-11)

free × healthy 的 Manage 展开区最初放的是**裸 "升级 Pro" 按钮 + 充值档位**——
被 Owner 抓包:这正是重设计要消灭的"两个平级花钱按钮无差异说明"布局,只是
藏深了一层。修复:展开区换成与 low 态**同一张 UpsellCard**(价值主张随 CTA
走,任何出现订阅按钮的地方都必须带"为什么");充值描述加定性区分
(rechargeDesc:"一次性充值,无需订阅")。裸按钮删除后 `subscribeBtn` i18n
key 变死键,同步删除(en/zh)。教训:**价值主张不是某个状态的装饰,是订阅
CTA 的固定伴生物。**

## 上下游

- 消费方:仅 [[NetmindAccountPanel]]。
- `PRICING_URL`(netmind.ai/pricing)随迁至此;`platform.openExternal` 打开。
