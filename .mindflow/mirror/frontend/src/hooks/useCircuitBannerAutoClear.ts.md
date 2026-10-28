---
code_file: frontend/src/hooks/useCircuitBannerAutoClear.ts
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review 第四轮 I-2）— 从「清 / 不清」升级为「同步真相」

轮询拿到的是权威状态，原来只用来回答要不要关横幅；`probing` 横幅在探测**失败**后（行回到
PAUSED）仍挂着「briefly cooling down / try again shortly」、没有 Resume 按钮、并每 30s 白轮询。
现在每次轮询走 `syncCircuitBannerReason(reason, cb_status, paused_reason)`：`null` → 关横幅；
返回的 reason 与当前不同 → `setCircuitOpen({agentId, reason: next})`（`probing` → `paused:auth` /
`paused:quota`，App.tsx 随之换成暂停文案并长出 Resume，之后按 `paused*` 继续轮询、修好即自关）；
相同 → 什么都不做。只在值真的变了才 set：`reason` 是 effect 依赖，同值 set 会每次轮询重建 interval。
锁：`a probing banner escalates to the real pause when the probe fails`（旧 hook 下红）、
`a paused banner that already shows the reason is not re-set on each poll`。

# useCircuitBannerAutoClear.ts — "paused" / "probing" 熔断横幅的自愈轮询

## Why it exists

熔断横幅（App.tsx，`narranexus:agent-circuit-open` 事件驱动）原本从不回查真实状态：
后端半开探测（[[circuit_breaker]]）成功、或 owner 在别的 tab 修好了 key，横幅仍挂着。
2026-09-09 版把轮询写成 App.tsx 里的一个 `useEffect`，预审指出两处缺陷，本 hook 是把
它抽出来修掉并能用 fake timers 单测的结果：

- **依赖收窄**为 `(agentId, reason, isLoggedIn)`，不再依赖 `circuitOpen` 对象本身。
  每一次被拒的 turn 都会派发新 detail 对象；按对象依赖会在用户每次重试时重建
  interval，30s 永远数不满——最需要自愈的场景恰好永远不轮询。
- **登录门禁**：未登录时不轮询鉴权端点（只会刷 401），并直接清掉横幅（App 作为根组件
  不会卸载，登出没有别的路径清它）。
- 挂上即查一次，再每 `CIRCUIT_BREAKER_POLL_INTERVAL_MS` 一次（同文件的过期检查 effect
  是同样的 `check(); setInterval(check)` 范式）。
- 轮询 `paused*` 与 `probing` 两类原因（PR #394 review I2）：`probing` 是另一个 turn 正持有
  半开探测，结果几秒到几分钟后落定，除了轮询没有别的路径会关掉这条横幅；探测期间
  `shouldClearCircuitBanner('probing')` 为假，横幅保持，出结果即同步（成功 → 自关；失败 → 升级为
  `paused:<reason>`，见最上一节）。
  cooling 短暂、随下一次交互自然消失，不轮询。
- 拉取失败保持横幅原样，下一 tick 重试——网络抖动不能误清一个真的还 paused 的横幅。

判定本身在 `services/wsCircuitOpen.syncCircuitBannerReason`（内部用
`shouldClearCircuitBanner`：`active`/`cooling` 才清，`probing` 不清）；这里只负责节奏与门禁。

## Upstream / downstream

App.tsx 调用 `useCircuitBannerAutoClear(circuitOpen, setCircuitOpen, isLoggedIn)`；
轮询 `api.getAgentCircuitBreaker`（`GET /api/agents/{id}/circuit-breaker`）。

## Tests

`hooks/__tests__/useCircuitBannerAutoClear.test.ts`（fake timers，mock 的是 `api`，不是
判定函数）：立即查 + 30s 后 active 清横幅；同 agent+reason 的新对象不重置 interval；
拉取失败不清、下 tick 重试；probing 横幅轮询且探测期间不清、active 后清；cooling 不轮询；登出清横幅且不再轮询；横幅消失即停。
