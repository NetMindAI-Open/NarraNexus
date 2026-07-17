---
code_file: backend/routes/office_watch_proxy.py
last_verified: 2026-07-13
stub: false
---

# office_watch_proxy.py — office artifact 实时预览的反向代理

## 为什么存在

让浏览器能同源地看到 `officecli watch` 起的实时预览服务(自刷新 HTML + SSE),从而把
office 文档(pptx/docx/xlsx)作为一种 artifact **实时**渲染。是"office = 一种 artifact"
统一方案的后端部分(不再有独立的 live-preview 路径)。

## 两个 router

- **`router`(挂 `/api`,session 鉴权)** —— `GET /office-watch/open?artifact_id=`:
  按 artifact 打开预览。查 artifact(校验属主 + kind=OFFICE_LIVE_KIND)→ 解析其文件 →
  **确保 watch 在跑并拿回该文件被分配到的端口**(本地:`ensure_watch` 返回 port;云端:
  `ensure_executor` + POST executor `/watch/ensure` 返回 port)→ 用这个 port 铸签名 token →
  返回 `/api/public/office-watch-proxy/{token}/{port}/`。**端口由 `ensure_watch` 分配并返回,
  open 不再自己 hash**(2026-07-13:每文件专属端口,多文档并发不串台)。**按需重启**是刷新/重开
  可靠的关键(不依赖记住的、可能已死的端口)。
- **`router` 还有 `GET /office-watch/version?artifact_id=`** —— live 预览的**变化信号**:
  返回 office 文件的 `{mtime,size}`(本地直接 stat;云端问 executor `/watch/version`)。前端轮询
  它:mtime 前进但没收到内容型 SSE 帧时,说明 officecli 的 per-file resident 没共享(watch 从没
  真 live 刷)→ 重载 iframe 兜底。这是 SSE 顺滑路径背后的**正确性兜底**。`open` 与 `version` 共用
  `_lookup_office_file`(查 artifact + 鉴权 + 解析文件)。
- **`public_router`(挂 `/api/public`,免鉴权,token 即鉴权)** ——
  `GET /office-watch-proxy/{token}/{port}/{path}`:反代到 watch 服务。校验 token(取
  user_id + port)、端口 allowlist,然后流式转发。

## 关键设计

- **token in path 而非头**:`<iframe src>` 导航发不了 `X-User-Id`,所以 open(authed)铸一个
  HMAC token(载 user_id+port)放进 URL 路径;iframe 及页面自身的 SSE/子请求都带着它 →
  免鉴权公共路由校验。完全照搬 artifact 的 `view-token → /api/public/artifacts/raw/{token}`。
- **HTML 重写(`_rewrite_watch_html`)**:watch 页面硬编了**根绝对路径**
  (`EventSource('/events')`、`fetch('/')`、`/assets/...`),在子路径代理下会打到 SPA 根而
  失效。重写:注入 `<base href="{prefix}/">` + 一个把 EventSource/fetch 参数去掉前导斜杠的
  shim(根绝对→相对→按 base 解析)+ 改写静态 `src|href|action="/..."`。只重写 HTML 文档;
  SSE(`/events`)和资源原样流式透传。shim 里还注入 `.slide-notes{display:none!important}`
  —— watch 页面给 speaker notes 写死了设计宽度(960pt,未随幻灯片缩放),不隐藏会比幻灯片还宽,
  在 artifact tab 里溢出。shim 还**包了 EventSource**:收到内容型帧(action 非 selection/mark)时
  `parent.postMessage('officewatch-content')`,让前端的 mtime 兜底知道 SSE 正在刷新、别重复重载。
- **resize nudge(2026-07-14)**:watch 页面用 `transform: scale` 缩放幻灯片,只在加载时按
  `clientWidth/innerWidth` 测量一次、之后仅靠 window `resize` 重算(无 ResizeObserver)。桌面
  WKWebView 里 sandbox iframe 加载时往往还没到最终尺寸 → 首次测量算错 → 幻灯片缩放错 → CJK 文字
  挤成一字一行(看着像竖排),直到有东西触发重测。shim 加载后按 `[100,300,700,1500]ms` 补发几次
  `resize`,强制它按稳定后的容器重新缩放。浏览器里无害(重算到同尺寸)。仅在真实 WKWebView 复现,
  Chrome/playwright-WebKit 都正常。
- **CORS 头(`_CORS_HEADERS = {Access-Control-Allow-Origin: *}`)挂在两个响应上**:
  viewer 的 iframe 用 `sandbox="allow-scripts"`(不透明源),页面里的 `EventSource('/events')`
  因此是**跨源**请求 → 不加 CORS 头会被浏览器拦掉,表现为"初始 HTML(第一张幻灯片背景)加载
  了,但 agent 后续每次编辑的 SSE 增量刷不进来,直到最后重挂 iframe 才整体显示"。HTML `Response`
  和 SSE `StreamingResponse` 两条路径都要带。
- **SSE 流式纪律**:仿 `manyfold_files`(`StreamingResponse` + `X-Accel-Buffering: no`)+
  `remote_agent_loop_driver` 的 `aiohttp.ClientTimeout(total=None)`,SSE 帧实时穿透不缓冲。
- **按用户 SSE 上限(`MAX_SSE_STREAMS_PER_USER`,`_register_sse_stream`)**:每个开着的预览 tab 占一条
  常驻 `/events` 长连,无上限的话一个用户(或泄漏的 token)能堆满后端连接/fd。第 N+1 条开时**挤掉
  该用户最旧的一条**(关它的 aiohttp session → 那条的 body 生成器结束、连接断,浏览器 EventSource
  收到 close)。**按用户**隔离——挤不到别人。只有 `text/event-stream` 计数,短命的资源请求不算。
  这是"多用户 SSE 连接不设防"这个安全/资源隐患的落地(桌面 `/events` 被 Rust 短路,不经此)。
- **云端 upstream**:`_resolve_upstream` 用 `ensure_executor(user_id)` 拿该用户容器 URL,
  转发到 `{executor}/watch/{port}`。跨用户隔离天然成立(只返回自己容器)。

## 上下游

- **被谁用**:`backend/main.py` 注册 `router`(/api)+ `public_router`(/api/public);前端
  `officeWatchApi.open` 调 open;`OfficeWatchViewer` 的 iframe 打公共代理。
- **依赖谁**:`_office_watch_token`(HMAC)、`utils/office_watch`(kind/端口/ensure_watch)、
  `broker_client`(云端 executor)、`ArtifactRepository`(open 查 artifact)。

## Gotcha

- open 里 import `ArtifactRepository` 等是函数内延迟 import(避免启动期循环依赖)。
- `/api/public/` 在 `backend/auth.py` 的 `AUTH_EXEMPT_PREFIXES` 内,公共代理才免鉴权。
