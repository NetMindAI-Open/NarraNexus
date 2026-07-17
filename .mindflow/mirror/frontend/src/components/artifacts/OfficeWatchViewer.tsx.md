---
code_file: frontend/src/components/artifacts/OfficeWatchViewer.tsx
last_verified: 2026-07-13
stub: false
---

# OfficeWatchViewer.tsx — office artifact 的实时渲染器

## 为什么存在

`ArtifactRenderer.RENDERER_BY_KIND` 里 `application/vnd.officecli-live` kind 对应的渲染器。
office 文档(pptx/docx/xlsx)注册成 artifact 后,不像其它 artifact 那样渲染静态文件,而是
渲染**实时预览**:向后端要一个(按需重启的)watch,用 iframe 指向 token 签名的代理 URL,
随 agent 编辑经 SSE 自刷新。

## 流程

挂载(或 `artifact.updated_at` 变化)时调 `officeWatchApi.open(artifact.artifact_id)`(带重试,
避免 watch 刚 idle 死、重启中途闪 "could not open")→ 后端确保 watch 在跑并返回签名 URL →
设 iframe `src`。iframe 用 `sandbox="allow-scripts"`(不透明源,和 HtmlRenderer 一致)。

## 刷新是混合的(HYBRID)

officecli 的 per-file resident 按**原始 (cwd,路径字符串)** 做键——只有 agent 用与 watch 一致的
路径编辑,SSE 才推内容帧、页面才平滑真 live。为了在路径漂移/SSE 断连时也不 stale:

- **主路径 SSE**:代理注入的 shim 在每个内容帧 `postMessage('officewatch-content')`;组件监听后
  记 `lastContentSseAt`。SSE 正常时**完全不重载**,无闪烁。
- **兜底 mtime**:每 2s 轮询 `officeWatchApi.version()` 拿文件 mtime;mtime 前进**且** 4s 内没收到
  内容帧(SSE 没送达)→ 重载 iframe。重载**走 `open()` 重新 mint**(不是 cache-bust 旧 URL):桌面无
  SSE 保活,watch 空闲会 idle 自停,复用死端口会 502 "watch server unavailable";re-open 会重新
  ensure watch。`key` 保持稳定(只换 src,不 remount)。

## 关键点

- **为什么先 mint 再 iframe**:`<iframe src>` 导航发不了 `X-User-Id`,所以先经 authed 的
  `open`(前端 fetch 带头)拿到路径含 token 的 URL,再指 iframe;页面的相对子请求靠后端注入的
  `<base>` 带上 token 前缀。
- **按 artifact 打开(不是记端口)**:后端按需重启 watch,所以刷新 / watch idle 停后都能重连
  ——这是"live 预览不该依赖记住的端口"这个教训的落地。
- **高度**:根 `h-full`(不是 `flex-1`)填满 artifact 内容区,iframe 再 `flex-1 min-h-0` 到底。
- **Tauri 桌面**:WKWebView 拦混合内容 http iframe → 桌面把 http open URL 经 `toDesktopScheme`
  转成 `officewatch://localhost` 前缀,走 Rust 自定义协议代理(见 `office_watch_scheme.rs`)。
  该 scheme 撑不住 SSE,所以桌面**没有 SSE 帧**、`lastContentSseAt` 恒为 0 → 上面那套 mtime 轮询
  **每次 mtime 前进都重载 iframe**,成为桌面唯一的更新机制(轮询节奏,非逐帧丝滑)。同一套混合逻辑
  自然降级,无需给桌面单独写分支。

## 上下游

- **被谁用**:`ArtifactRenderer`(office-live kind → 本组件)。因此白拿 artifact 的一切:
  平级 tab、最小化/恢复/放大/删除、DB 持久化、ChatPanel 的 register_artifact 识别器。
- **依赖谁**:`officeWatchApi.open`;`lib/tauri.isTauri`。

## 历史

早先版本是独立系统(officeWatchStore + 独立 tab + 独立 signal 工具 + 文件树"实时预览"
按钮)。2026-07-13 按 Owner 判断**合并进 register-as-artifact**——两个"preview"概念让人
困惑。现在只有一个概念(artifact),office 恰好实时渲染。

2026-07-13 晚:桌面从"暂不支持"占位升级为经 `officewatch://` 自定义协议渲染(轮询重载版)。

