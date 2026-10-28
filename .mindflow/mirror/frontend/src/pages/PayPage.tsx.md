---
code_file: frontend/src/pages/PayPage.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 不再自己建卡结账，改为把轨道选择交给账号面板

`/pay` 是官网定价页 CTA 的落点，原来直接 `api.subscribe()`（无参 = 信用卡）。
**对这次改动要服务的人来说那是一条死路**：支付宝和微信根本付不了 Stripe 订阅，
他们从定价页点「买 Pro」，落在一个只能刷卡的页面上。

现在 `navigate('/app/settings?tab=account&intent=buy')`，由
[[NetmindAccountPanel]] 的购买弹窗去问「哪条轨、几个月」。

**为什么是跳转而不是在这里再放一个选择器**：那个问题带着真实规则（卡不能带
months、一次性上下界 1–12、一次性生效期间卡要撤下），第二份实现一定会和第一份漂移。
`intent=buy` 让弹窗**直接打开**——落在设置页上"再点一下就能买"只是把死路挪了个位置，
没有消除。

随之失效的：原来那张"建 session → 桌面端 openExternal / Web 端 location.replace"的
分支表，以及 401 / "already subscribed" 的兜底跳转。

**文件头也重写了**（评审 🟢-1）：它有 40 行，其中约 25 行在讲已经删掉的机制 ——
same-tab `location.replace`、Tauri 分支、错误态与重试，还专门论证了「为什么用
`replace` 而不是 `assign`」，而这个文件现在一次都不引用 `location`。这和
[[NetmindAccountPanel]] 那条「一条能被自己文件证伪的理由比没有理由更糟」是同一件事，
判断也该同样适用于文件头。现在头部只留「为什么需要这个路由」和四条去向。

**并且整个错误态一起删了**：探测自己吞掉异常、`navigate` 不抛，所以这个页面停止建
结账之后，外层 catch **没有任何东西可接**——`phase='error'`、错误卡片、重试按钮
全部不可达。留着就是给一个不可能发生的状态维护 UI（铁律 #8）。这个页面现在就是
它真实的样子：**一个转场 spinner**。订阅探测保留，仍是 defensive-only——它让已订阅
的用户落在账号页而不是带着 `intent=buy` 弹出购买框。


## 2026-08-10 — direct-pay funnel facts

The bounce route records the same click/open stages as the account panel.
Active-subscription redirects and failed checkout creation do not emit opened.

# PayPage.tsx — /pay 官网直达 Stripe 的中转路由

## 为什么存在

P0「付费流程断裂」(deadline 2026-08-08):官网点套餐后要穿过设置页 →
manage 弹窗 → 升级按钮三层才到 Stripe;未登录用户登录后还会丢失付费意图。
Owner 拍板方向:**官网点击充值直接跳付款页面**。硬约束是 checkout session
由 NetMind 计费 API 铸造、必须带用户 loginToken,官网(无登录态静态站)
拿不到——所以"直跳"的真实形态是"官网 → 最小认证跳板 → Stripe",本页
就是这块跳板:无 UI(只有 spinner),挂载即 subscribe → checkout_url →
同 tab `location.assign`。

## 分支表

| 状态 | 行为 |
|---|---|
| 已登录 × free | 订阅探测非 ACTIVE → subscribe → 直跳 Stripe |
| 未登录 | ProtectedRoute → `/login?next=%2Fpay`,登录/注册后回来续跳(next 链路三条登录路径都支持,LoginPage 已有) |
| 已订阅(探测 ACTIVE,或 subscribe 答 400 already-subscribed) | `/app/settings?tab=account`(去管理,不重复铸 session) |
| 非 Power 账号(无 netmindToken) | 同上——没 token 没法付,账户页会解释账号状态 |
| billing 401(loginToken 过期) | 同上——重试永远救不活死 token,重新链接归账户面板管 |
| subscribe 失败(其它) | 行内错误 + 重试 + 账户页链接,绝不静默死胡同 |

## 设计决策

- **同 tab `location.replace`,不是 assign、也不用 openExternal**(review
  修正 2026-07-31):assign 会把 /pay 压进历史,用户在 Stripe 按返回键 →
  重挂载 → 再铸一个 session 再弹回 Stripe(死循环);bfcache 恢复则是
  永久 spinner。replace 把 /pay 从历史抹掉,从 Stripe 返回落到用户真正的
  来处(官网 pricing / login)。Stripe 付完的 return URL 回
  `/app/settings?tab=account`(billing.py::_return_urls 已有),闭环。
- **桌面端(Tauri)例外**(铁律 #7):webview 导航去 Stripe 会让窗口
  回不来(return URL 指云端 web app)→ `platform.openExternal` 开系统
  浏览器,webview 落账户页。今天桌面端没有入口链到 /pay,属防御。
- **订阅探测只是防御,失败=未知而非失败**(review 修正):真正的防重
  不变量在服务端(subscribe → 400 "Already subscribed")。探测挂了照样
  往下走 subscribe,付费主路径不为一次只读查询的抖动陪葬。
- **inFlight ref 防重**:StrictMode 开发态 effect 双触发,不能铸两个
  checkout session;重试路径同样过这个闸(防的是同一次挂载内并发;
  跨挂载的返回键场景由 replace 消灭)。
- checkout_url 的 host 白名单(仅 *.stripe.com)在后端
  `billing.py::_validate_checkout_url` 把守,前端不重复校验。

## 上下游

- 入口:narranexus-website `app/pricing/page.tsx` 的 Pro 卡 CTA 和页底
  get_started CTA(`PAY_URL`);top-up CTA 走 `?tab=account` 不经过本页。
- 路由注册:App.tsx `/pay`(ProtectedRoute 包裹,lazy)。
- i18n:`pages.pay.*`(zh/en)。
- 测试:`pages/__tests__/PayPage.test.tsx`(10 个分支)。
