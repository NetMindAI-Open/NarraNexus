---
code_file: frontend/src/lib/tauri.ts
last_verified: 2026-04-13
stub: false
---

# lib/tauri.ts

## 为什么存在
轻量 Tauri IPC wrapper，供 dashboard 使用。通过 `window.__TAURI_INTERNALS__` / `window.__TAURI__` 全局对象调用——不依赖 `@tauri-apps/api` npm 包（该包未加入 frontend dependencies）。

## 导出
- `isTauri()` — 检测是否运行在 Tauri webview
- `setTrayBadge(count)` — 更新 tray 图标数字，web 模式 no-op
- `listenTauri(event, handler)` — 订阅 Tauri 事件（如 `tauri://blur`），web 模式返 null

## 设计决策
- 两侧 clamp [0, 999]：前端一层（这里）+ Rust 一层（`commands/tray.rs`），防脏数据
- 所有失败 swallow：tray 是 cosmetic，失败不升级为 UI 错误

## Gotcha
- 未来如果要加 `@tauri-apps/api` 依赖，这个 wrapper 可以完全替换为官方 SDK。但当前阶段保持零 npm 依赖更轻
