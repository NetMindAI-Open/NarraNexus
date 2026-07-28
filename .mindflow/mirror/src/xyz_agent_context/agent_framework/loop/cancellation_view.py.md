---
code_file: src/xyz_agent_context/agent_framework/loop/cancellation_view.py
last_verified: 2026-07-27
stub: false
---
# loop/cancellation_view.py — 取消检查的唯一归一点

## 为什么存在

driver 收到的 `cancellation` 是 `Any`，各家曾各自猜 API：remote 读
`is_cancelled`（真实 CancellationToken 的 bool property），codex v2 调
`is_set()`（asyncio.Event 风格）——**而真实 token 没有 is_set 方法，
codex v2 的取消检查因此是恒 False 的死代码**（进程内 codex turn 无法被
打断，2026-07-27 发现并修复）。本类收敛所有形状，driver 只问
`view.requested()`。

## 设计约束

- **只读侧**：触发取消永远在 owner（agent_runtime 的
  `CancellationToken.cancel()`）；driver 只观察不取消（铁律 #15——
  平台不能成为打断源）。
- 优先级：`is_cancelled` property（文档化 API）> `is_set()` 方法
  （event 形状兜底）> 其余永不取消。
- `await_cancelled()`（异步等待侧）不经过本类，仍由 claude/cli_sdk
  直连——本类只管步边界轮询。
