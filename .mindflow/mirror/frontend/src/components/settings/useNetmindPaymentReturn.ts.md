---
code_file: frontend/src/components/settings/useNetmindPaymentReturn.ts
last_verified: 2026-07-30
stub: false
---

# useNetmindPaymentReturn — 付款回跳的消费者

## 为什么存在

Stripe 付完款把用户送到 `?tab=account&status=…&flow=…`（这个 URL 由后端写进
Checkout Session，见 [[billing]] 的 `_return_urls`）。**回跳落地的这个标签页是全新
挂载、没有任何轮询在跑**，所以付款那一侧原本由轮询承担的后续动作，在这里必须重新
安排一次 —— 这就是这个 hook 的全部职责。

从 [[NetmindAccountPanel]] 抽出：那个文件已经越过 800 行上限，而它本身已经有把
`NetmindActionZone` / `NetmindTopUpControls` / `NetmindRunwayView` 拆出去的成例。

## 为什么在渲染期解析，而不是在 effect 里 setState

`notice` 由 `useState` 的惰性初始化器从 query 解析出来（纯函数 `parseReturnNotice`），
effect 只负责副作用。抽成 hook 时 `react-hooks/set-state-in-effect` 报了原来那种写法
（同一段代码留在面板里时这条规则没报，是抽取把它暴露出来的），而规则说得对：

- effect 里 setState 会多一轮级联渲染 —— 于是「我的钱到了吗」这条通知在**第一帧不
  存在、第二帧才出现**。它恰恰是用户带着进来要看的东西。
- `useState` 初始化器「只跑一次」是**构造上成立**的，不需要一个 flag 去维持。

代价：`enabled` 只在首屏被读一次。这和 [[SettingsPage]] 处理 `?tab=` 是同一套取舍，
成立的前提是 configStore 同步 hydrate（无 `skipHydration`、默认 localStorage）。

## 三个不显然的判断

- **success 时不立刻重读**。面板的挂载 effect 在同一个 tick 已经把那五个请求发过
  了，再读一遍只是翻倍、并不更新鲜。真正需要的是**延迟一次**（`RETURN_SETTLE_MS`）
  —— Stripe 在它自己完成的瞬间就跳转，比 NetMind 记账早一拍。**跳转不是收据。**
- **不直接调 `linkNetmind`**，改为交给 `watchPlanFlip`（= 面板的
  `pollUntilActive`）。此刻 NetMind 可能还没把订阅标成 ACTIVE，直接 link 失败会给
  一个刚付款成功的人显示报错；轮询的 ACTIVE 分支本来就是自动接入的正确位置。
- **延迟重读必须自己占一个 effect**（key 是 `notice`，只会被设置一次）。放进上面那个
  effect 会被自己清掉：剥 query 会让它重跑，而 cleanup 先执行。unmount 时清定时器
  —— 孤儿定时器打进已卸载的树，正是事故教训 #2 的形状。

## 调用契约（会静默坏掉的那种）

`refresh` / `watchPlanFlip` **必须引用稳定**。传内联箭头函数会让 settle effect 每次
渲染都重新 arm 定时器，于是它**永远不会触发** —— 没有任何报错，只是回跳后余额不再
更新。所以这两个参数是分开的位置参数而不是一个 options 对象：对象字面量每次渲染都是
新身份，等于把这个坑做成默认行为。

## 上下游

- 上游：[[NetmindAccountPanel]]（唯一调用方），传 `isPowerUser` / `load` /
  `pollUntilActive`。
- 下游：`useSearchParams`（读 + 剥参数）；渲染交给 [[NetmindReturnNotice]]。
- 参数来源：[[billing]] 的 `_return_urls`，两边的 `status` / `flow` 取值必须一致。
