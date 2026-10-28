---
code_file: frontend/src/lib/sessionGuard.ts
last_verified: 2026-08-06
stub: false
---

# sessionGuard.ts — 强制登出前的第二意见

## Why it exists

登出是前端对自己做的最具破坏性的操作：`configStore.logout()` 清身份 →
`ProtectedRoute` 把整棵受保护子树换成 /login → 所有面板卸载、WS 断开、
内存态清零。2026-08-02 线下活动上，**单个 401 就足以触发它**，而且反复触发；
用户把这次拆毁描述成"整个页面重新加载了、很混乱"（实际上没有任何真正的
reload——前端全程不碰 `window.location`，所以服务端日志里也查不到痕迹，
这条"查无痕迹"当时被误读成排除项，其实正是本条链路的佐证）。

于是"会话死亡类 401"被降级为**怀疑**而非判决：先问一次
`GET /api/auth/session`（[[auth]] 路由，不查库），只有它也说死了才拆。

## Design decisions

**只有 `dead` 才动手；`alive` / `unknown` 一律不动。** 探针返回 5xx、超时、
断网都归为 `unknown`。理由是不对称的代价：误判"没死"只让用户损失一个失败
请求，误判"死了"让他损失屏幕上的一切。断网时把人踢下线是同一个核弹按钮，
只是换了触发源。

**去抖 + 闩锁。** 页面挂载会同时发十几个请求，JWT 一死就是一**批** 401。
没有 `inFlight` 就是 N 次探针 + N 次登出 + N 个横幅；没有 `confirmed` 闩锁，
同一批的迟到 401 还会再探一次已经拆掉的会话。`resetSessionGuard()` 在
[[configStore]] 的 `login()` 里调用——新凭证必须让上一次判决失效。

**事件带上 `detail: {endpoint, code}`。** 8/2 之后我们手里只有一串没有原因的
401，客户端侧则什么都没有。现在 [[App]] 的处理器会把触发端点和原因码打进
console，下一次现场至少能一句话定位。

**WS 也走这里。** [[wsAuthError.ts]] 不再直接派发登出事件——WS 的 AuthError
帧里混着"local 模式 user_id 对不上"这种前端状态 bug，让探针来裁决，误判的
代价就从"整个会话"降到"这一次运行"。

## Upstream / Downstream

- **调用方**：[[api.ts]]（REST 401 且 `isSessionDeadFailure`）、
  [[wsAuthError.ts]]（任意 AuthError 帧）。
- **依赖**：[[authHeaders.ts]]（探针要带同样的身份头）、
  `runtimeStore.getApiBaseUrl`。
- **监听方**：[[App]] 的 `narranexus:auth-expired`。
- **测试**：`frontend/src/lib/__tests__/sessionGuard.test.ts`、
  `api.authFailure.test.ts`。

## Gotchas

**不要用 `api.ts` 发探针。** 探针必须是裸 `fetch`，否则它自己的 401 会再次
进入 `request()` 的 401 分支 → 递归。这也是 `getAuthHeaders` 被抽到
[[authHeaders.ts]] 的原因（`api → sessionGuard → api` 会成环）。

**没有任何身份头时直接返回。** 匿名探测和登录前的请求不该走到拆毁路径上。
