---
code_file: src/narranexus/kernel/plugins/lifecycle.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — `clear_crashes()`

崩溃预算按「连续崩溃」计（第二次即禁用）：干净 boot 恢复后计数清零、`last_error` 清空、auto-disabled 警告移除；由
`hosts.boot` 在 crashed/slow → registered 的恢复步调用。

## 2026-09-03（批 2b.1）— `registry.json` 的唯一写入者 + LKG + 状态机 + 启动标记

用文件不用库：回滚必须在「没有 LLM、没有 UI、没有 DB」时也能做，所以插件真相是内核能用纯文件操作改写的
JSON。每次 `RegistryStore.update`：文件锁（fcntl）→ 把当前文件复制成 `registry.lkg.json` → 写临时文件 →
`os.replace`；中途崩溃只会留下旧文件或新文件，不会留下半个（测试模拟 `os.replace` 失败）。
状态机 `registered→validated→enabled→active` 只能按序前进；异常态（incompatible/blocked/missing/
deps_missing/crashed/disabled/slow）任意可进，出来只能回 `registered` 重新校验。`record_crash` 第二次自动
disable 并写 warning；`set_enabled(True)` 清零计数。`BootMarker`：`enter` 时标记残留=上次没到健康，连续两次
`safe_mode_due`。`BisectState` 是二分定位的持久化形状（逻辑在 `bisect.py`）。

## 2026-09-07 — LKG = last state that reached health; acknowledged_permissions

update() no longer copies the registry to registry.lkg.json before every write: a boot itself writes (rejections, crash counts, validated/enabled), so the snapshot-before-write LKG was the state INCLUDING the plugin that just broke the boot, and rollback restored it. snapshot_lkg() copies the file explicitly; BootReport.mark_healthy() calls it. PluginRecord.acknowledged_permissions lists the permission tokens the user's acknowledgement covered, so an upgrade that declares more can be told apart.
