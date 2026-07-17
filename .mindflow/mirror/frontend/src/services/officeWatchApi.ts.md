---
code_file: frontend/src/services/officeWatchApi.ts
last_verified: 2026-07-13
stub: false
---

# officeWatchApi.ts — 铸造 office 实时预览的 iframe URL

## 为什么存在

`OfficeWatchViewer` 打开一个 office artifact 的实时预览时,不能直接把 iframe 指向代理
(那样 `<iframe src>` 导航发不了 `X-User-Id` → 401)。本模块的 `open(artifactId)` 走 session
鉴权(`authHeaders()` 注入 X-User-Id / JWT)调后端 `/api/office-watch/open`,后端确保 watch
在跑并返回一个**路径含签名 token** 的 URL;之后 iframe 及页面自身的子请求都靠 token 鉴权。
形状与 `artifactsApi.getRawUrl` 一致。

## 两个方法

- **`open(artifactId)`** —— mint 签名 iframe URL(下述)。
- **`version(artifactId)`** —— 拿文件 `{mtime,size}`,给 `OfficeWatchViewer` 的 mtime 兜底轮询用:
  mtime 前进但没收到内容型 SSE 帧时重载 iframe(SSE 静默失效时的正确性兜底)。

## 关键点

- **按 artifactId 打开**(不是文件/端口):后端按需(重)启 watch,所以刷新/重开可靠。
- 返回**绝对** URL(`getApiBaseUrl() + raw_url`)——dmg 里源是 `tauri.localhost`,相对路径会
  解析错。
- 复用 `artifactsApi.authHeaders`(单一鉴权头来源)。

## 上下游

- **被谁用**:`OfficeWatchViewer`。
- **依赖谁**:后端 `GET /api/office-watch/open`;`artifactsApi.authHeaders`;`getApiBaseUrl`。
