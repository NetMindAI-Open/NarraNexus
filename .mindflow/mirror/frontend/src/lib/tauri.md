---
code_file: frontend/src/lib/tauri.ts
last_verified: 2026-05-05
stub: false
---

# lib/tauri.ts

## 为什么存在
轻量 Tauri IPC wrapper，供 dashboard 与 settings 模块使用。通过
`window.__TAURI_INTERNALS__` / `window.__TAURI__` 全局对象调用——不依赖
`@tauri-apps/api` npm 包（该包未加入 frontend dependencies）。

## 导出
- `isTauri()` — 检测是否运行在 Tauri webview
- `setTrayBadge(count)` — 更新 tray 图标数字，web 模式 no-op
- `listenTauri(event, handler)` — 订阅 Tauri 事件（如 `tauri://blur`），web 模式返 null
- `triggerClaudeLogin()` — 调起 Tauri 端的 `claude auth login`，web 模式返 null
- `triggerClaudeLogout()` — 调起 Tauri 端的 `claude auth logout`，web 模式返 null
- `cancelClaudeLogin()` — SIGTERM 当前在跑的 `claude auth login` 子进程，配合前端 600s 倒计时使用
- `getClaudeLoginStatus()` — Tauri 端登录态快照（不阻塞，但前端目前主要用后端 `/api/providers/claude-status`，这个保留作为 backend-down 时的兜底）

## 设计决策
- 两侧 clamp [0, 999]（tray badge）：前端一层（这里）+ Rust 一层（`commands/tray.rs`），防脏数据
- tray 失败 swallow（cosmetic）；OAuth 失败 throw（用户需要看到 toast）
- 默认所有 wrapper 在非 Tauri 环境返回 null，调用方靠 `isTauri()` 决定 UI 分支

## Gotcha
- 未来如果要加 `@tauri-apps/api` 依赖，这个 wrapper 可以完全替换为官方 SDK。但当前阶段保持零 npm 依赖更轻
- `triggerClaudeLogin/Logout` 是阻塞式 IPC（CLI 等用户在浏览器完成 OAuth），调用方必须显示 loading 态、避免并发触发，否则会让用户错以为 UI 卡死
