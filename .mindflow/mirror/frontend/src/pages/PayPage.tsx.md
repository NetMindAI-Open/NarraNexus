---
code_file: frontend/src/pages/PayPage.tsx
last_verified: 2026-07-31
stub: false
---

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
