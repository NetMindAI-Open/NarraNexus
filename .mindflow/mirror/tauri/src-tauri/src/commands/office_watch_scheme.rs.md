---
code_file: tauri/src-tauri/src/commands/office_watch_scheme.rs
last_verified: 2026-07-14
stub: false
---

# office_watch_scheme.rs — `officewatch://` 自定义协议(桌面实时预览)

## 为什么存在

桌面(dmg)实时 Office 预览要在 iframe 里加载 officecli-watch 页面。webview origin 是
`https://tauri.localhost`,后端在 `http://localhost:8000` → WKWebView 把这个 http iframe 当
**active mixed content 静默拦掉**(和 artifact 同一个 P0)。

静态 artifact 用 base64 blob 绕过(见 `artifact_fetch.rs`),但 blob 是**静态快照**;watch 页面
要拉自己的子资源(assets/字体/katex)还有一个 SSE 端点,blob 一个都带不了。

所以这里换**自定义协议**:custom scheme 不算混合内容,webview 会加载;页面自己的**根相对**子请求
经后端注入的 `<base>` 解析回 `officewatch://` 也落到这里,于是每个资源都由 Rust 代理。Rust 自己
发起的 HTTP 不受 WKWebView 约束。

## 关键设计

- **不能流式 SSE**:Tauri 自定义协议的 responder 只应答一次、`respond` 消费 self,撑不住 SSE 长连
  (实测 Windows 不支持、macOS 实验性)。所以对页面的 `/events` 请求**直接返回空**(桌面无 SSE
  推送),实时更新改由前端 `OfficeWatchViewer` 的 **mtime 轮询 → 重载 iframe** 驱动(watch 首页
  GET 永远渲染当前文档)。这是"稳妥版"取舍:每次内容变化刷新一次(轮询节奏),非逐帧丝滑。
- **URL 映射**:`officewatch://localhost/api/public/office-watch-proxy/{token}/{port}/...` 1:1 映到
  `http://localhost:8000/api/public/office-watch-proxy/...`。取 `uri.path()+query` 拼到 BACKEND。
- **SSRF 护栏(两段)**:`path` 后面会**原样**拼进 `format!("{BACKEND}{path}")`,所以先做两道检查再放行。
  ①**先拒 `..` 点段**(`path.split('/').any(|seg| seg == "..")`)——若某平台 webview 不为自定义 scheme
  归一化点段,`/api/public/office-watch-proxy/../../secret` 可能通过前缀检查却落到任意后端路由;按
  段切分只拦 `..` 段、不误伤合法含点文件名。②**再查前缀**:只代理
  `/api/public/office-watch-proxy/`,其它一律 403。token 即鉴权(后端 `open` 铸,放在路径里),和浏览器
  完全一致;只打 loopback :8000。
- **CORS**:iframe 用 `sandbox="allow-scripts"`(不透明源),它对 `officewatch://` 的请求是跨源 →
  和浏览器代理一样加 `Access-Control-Allow-Origin: *`(auth 是路径里的 token,不是 cookie)。

## 上下游

- **注册在**:`lib.rs::run()` 的 `.register_asynchronous_uri_scheme_protocol("officewatch", ...)`
  (spawn 到 async runtime 里跑 `handle`,再 `responder.respond`)。
- **被谁用**:`frontend/.../OfficeWatchViewer.tsx` —— `isTauri()` 时把后端 http open URL 转成
  `officewatch://localhost` 前缀(`toDesktopScheme`)当 iframe src。
- **依赖**:`reqwest 0.12`(loopback http,无 TLS,和 artifact_fetch 同款);`tauri::http`。

## Gotcha

- **只 macOS**:NarraNexus 桌面只发 macOS(`targets: ["dmg","app"]`,CI 只 build-macos),所以
  custom-scheme 的跨平台流式限制不影响我们;但也因此这条路**只在 WKWebView 里能验**,改完必须
  `tauri dev` / dmg 肉眼确认。
- **后端端口硬编 8000**:和 `artifact_fetch.rs` / `port_preflight.rs` 一样。后端若改动态端口,三处
  一起改。
- 桌面无 SSE → `OfficeWatchViewer` 里 `lastContentSseAt` 恒为 0 → 每次 mtime 前进都会重载(这正是
  桌面期望的行为,不是 bug)。
- **`build()` 不允许 panic**:异步 scheme handler 里若 `Response::builder().body()` 返回 `Err`
  (上游 Content-Type 带非法头字节),旧代码 `.expect()` 会 panic;tokio 会**静默吞掉**该 task →
  `responder.respond` 永不调用 → webview 无超时地挂起、无可见错误。现在唯一由调用方控制的头
  (Content-Type)先用 `HeaderValue::from_str` 预校验,失败退回 `application/octet-stream`,其余头是
  静态常量、status 恒为合法 u16 → builder 必成功,`.expect` 永不触发。
