---
code_file: frontend/src/components/settings/NetmindTopUpControls.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 金额显示必须取整（真机实测抓到）

上游把钱当**高精度十进制字符串**回：`charge_amount = "67.531480"`、
`min_charge = "5.0"`（本地起栈打 dev 计费实测）。原来直接渲染，界面会出现
`充值 ¥67.531480` 和 `最低 ¥5.0`。

**单测当时全绿**，因为夹具里的 `"73.00"` 是我自己按两位小数编的 —— 这正是
"测试通过不等于对"的标准形态。夹具已换成真实形状，断言改成钉**取整后**的值。

`rate` 故意**不**取整：汇率的精度是信息，价格的六位小数是噪音。


## 2026-08-18 — 支付方式 + 微信的人民币换算

顶部多了 [[PaymentMethodChoice]]，微信选中时在金额行下方多一块换算说明。两个
拿捏过的细节：

- **换算金额同时印在按钮上**（`充值 ¥73.00`）。提交前最后读到的那个数，应该是
  银行真正划走的那个，而不是他自己敲进去的那个。
- **本组件不判断报价是否新鲜**。面板只会把「和当前金额匹配」的报价传进来，
  不匹配时传 `null`。职责边界没变（见下方原有条目）：涉及钱的守卫全在面板。

`fxLoading` 单独一个 prop 而不是用 `fx === null` 推断：「还没开始查」和「查完了
但没有结果」要显示的东西不一样。

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
