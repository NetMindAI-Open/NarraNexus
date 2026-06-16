---
code_file: frontend/src/lib/netmindAuth/tokenInbound.ts
last_verified: 2026-06-16
stub: false
---

# tokenInbound.ts — Power 登录态入站直通（场景 A）

## 为什么存在

用户从 netmind.ai 或 Arena 点链接进入 NarraNexus 时，链接带 `?token=<NetMind loginToken>`。因为我们与 Power 共用 sysCode（token 通用），只要把这个 token 接住、换成我们自己的会话，用户就免登进来了（Phase 1 的「Power 登录态打通」场景 A）。本模块是这条入站链路的纯函数工具，抄自 Arena 的 UserContext 入站处理。

## 上下游关系

**被谁用**：
- `frontend/src/main.tsx` 在首帧渲染前调 `captureInboundEntry()`，同步锁定真正的入口 URL（与 `initManyfoldFragmentAuth()` 同一 pre-render 模式）。
- `frontend/src/App.tsx` 的 bootstrap useEffect 调 `getInboundEntry()` 拿到这份已捕获结果，命中 token 则 `exchangeInboundToken` 换会话、写 configStore。

**调用谁**：`exchangeInboundToken` 调 `@/lib/api` 的 `api.netmindLogin(token, source)`（后端 `/api/auth/netmind-login` 验 token 换发我们 JWT）。

## 设计决策

- **入口捕获必须在渲染前同步完成**（`captureInboundEntry`）。这是 2026-06-16 的治本修复：原先 source/token 是在 App 的 mount `useEffect` 里读 `window.location` 的——但 React effect 是子先父后，未登录的 arena 入口下 `RootRedirect` 会**同步**渲染 `<Navigate to="/login">`，它的 navigation effect 先于 App 的 mount effect 跑，URL 已被改写成 `/login`，`?source=arena` 此时已经没了。结果：已登录能 provisioning、未登录登进来却查不到 source → 不创建 Agent。`captureInboundEntry` 在 `main.tsx` 的 `createRoot().render()` 之前跑，那一刻 `window.location` 还是真正的入口 URL。
- **`captureInboundEntry` 幂等**：用 module 级 `_inbound` 缓存首次捕获结果；`getInboundEntry` 只读不再解析 URL，保证后续读到的就是入口态而非被重定向后的 URL。
- **即取即删**：`takeInboundToken` 解析后立刻 `window.history.replaceState` 把 `token` 从 URL 抹掉（保留其余 query 与 hash），避免 loginToken 泄漏进浏览器历史/书签/截图。
- **source 透传**：同时读 `?source=`（如 `arena`），即使没有 token 也返回 source；`captureInboundEntry` 把它写进 sessionStorage（`ENTRY_SOURCE_KEY = 'nx-entry-source'`）供 arena landing provisioning 用（本模块只透传/暂存不消费）。
- **纯函数边界**：takeInboundToken 只做 URL 解析 + 副作用（replaceState），不碰网络；exchange 单独一个函数，便于测试分离（测试只测 takeInboundToken 的解析与抹除、captureInboundEntry 的暂存与幂等，网络换会话由 api 层测）。

## Gotcha

- App 的 bootstrap effect 在换会话前检查 `isLoggedIn`，已登录则跳过——避免覆盖现有会话。
- 换会话失败时静默 fall through 到登录页（catch 吞掉），不打断用户。
- arena landing flow 的 `arenaLanding.ts` 用同一字面量 `'nx-entry-source'`（其本地常量 `ENTRY_KEY`）读这个 key；两边必须保持一致，本模块导出 `ENTRY_SOURCE_KEY` 作为权威定义。
