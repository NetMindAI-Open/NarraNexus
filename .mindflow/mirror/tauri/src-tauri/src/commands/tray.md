---
code_file: tauri/src-tauri/src/commands/tray.rs
last_verified: 2026-04-13
stub: false
---

# commands/tray.rs

## 为什么存在
Tauri IPC 命令 `set_tray_badge(count)`。前端 dashboard store 检测 running count 变化时 invoke；Rust 端更新 `TrayIcon.set_title` 让用户在桌面右上角/右下角一眼看到"有几个 agent 在跑"。

## 设计决策
- **std::sync::Mutex**（不是 tokio）：tray ops 是 sync，不跨 await。setup 闭包也是 sync。tokio Mutex 在此无意义
- **try_lock + best-effort**：lock 争用时 skip 这次更新；tray 是 cosmetic，不能为更新等
- **clamp [0, 999] + "999+"**：macOS menubar 长数字撑破 layout（security M-4）。u32::MAX 不会 panic 是因为 Rust u32 + format! 天然安全
- **state.rs 持句柄**：`AppState::tray_handle: Arc<StdMutex<Option<TrayIcon>>>`；`lib.rs::setup` 把 `create_tray()` 返回值存入

## Gotcha
- 永远不要 `unwrap()` / panic；任何错误转成 log + Ok(())。tray 挂了 dashboard 不能挂
- 若 `tray_handle` 为 None（setup 时 tray 未成功创建），silently skip
- 注册处：`lib.rs::invoke_handler!` 的 `commands::tray::set_tray_badge`

## 测试
cargo test commands::tray::tests —— 5 个 clamp 单测（0 / 1 / 999 / 1000 / u32::MAX）
