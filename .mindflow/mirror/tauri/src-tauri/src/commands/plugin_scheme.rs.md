---
code_file: tauri/src-tauri/src/commands/plugin_scheme.rs
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d.2）— `plugin://<id>/<asset>` 自定义 scheme

桌面 webview 从插件 home（`NARRANEXUS_PLUGIN_HOME` 或 `$HOME/.narranexus/plugins`，与内核 `paths.py` 同规则）
只读地提供 `<id>/frontend/dist/` 下的文件：id 必须是 `<publisher>.<name>` 形状、路径段只允许普通段、
canonicalize 后仍须在 dist 内（symlink 逃逸拒绝）；否则 403。Content-Type 按扩展名。单元测试覆盖穿越/大写 id/
非插件目录。**本机没有 cargo，未编译验证**——发版前必须 `cargo test`（见 batch 报告）。
