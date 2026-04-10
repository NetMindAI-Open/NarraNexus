---
code_file: src/xyz_agent_context/agent_runtime/logging_service.py
last_verified: 2026-04-10
stub: false
---
# logging_service.py — 每个 agent turn 的独立日志文件管理

## 为什么存在

调试 agent 问题时需要查看某次对话的完整执行日志。如果所有 agent 的日志混在全局 loguru 输出里，很难按 agent/turn 过滤。这个服务为每次 `AgentRuntime.run()` 创建一个独立的 `.log` 文件（按 agent_id + 时间戳命名），并在 run 完成（含后台 Step 5-6）后自动清理文件 handler。

## 上下游关系

在 `AgentRuntime.__init__` 中实例化，在 `run()` 入口调用 `setup(agent_id)` 激活文件 handler，在后台任务（Steps 5-6）完成后的 `finally` 块中调用 `cleanup()` 移除 handler。

依赖 loguru 的 `logger.add()` 和 `logger.remove()` API。写入的是全局 loguru logger，所以所有模块的 `logger.info()` 在 handler 活跃期间都会同时写入文件和控制台。

日志目录默认是 `~/.narranexus/logs/agents/`，可以在 `AgentRuntime` 构造时通过 `logging_service=LoggingService(log_dir="...")` 覆盖。

## 设计决策

**必须用 loguru `{time}` placeholder 生成文件名**：如果用 `datetime.now().strftime(...)` 手动拼接时间戳，loguru 的 `retention` 机制无法识别该文件（retention 通过把 `{time}` 替换为 `.*` 做 glob 匹配，手动拼接的时间戳是固定字符串，不会被 glob 到）。结果是旧日志文件永远不会被清理，磁盘空间持续增长。这是文件顶部注释重点说明的坑。

**不设置 rotation**：每次 `setup()` 调用已经创建了新文件（通过 `{time}` 生成唯一文件名），不需要再 rotation 分割。如果设置了 rotation，loguru 的 retention 只在 rotation 触发时执行，而不设置 rotation 时，retention 在 sink 关闭（`cleanup()` 调用 `logger.remove()`）时执行，确保每次 run 结束都会清理过期日志。

**`cleanup()` 延迟到后台任务完成后**：Step 5-6 是后台执行的，它们的日志需要写入 agent 的 .log 文件。如果在 `run()` generator 结束后立即 `cleanup()`，后台任务的日志会丢失。所以 `cleanup()` 的调用权转移给后台 `_run_hooks_background` 的 `finally`。

## Gotcha / 边界情况

- `setup()` 在调用前会先调用 `cleanup()` 清理之前的 handler。如果 `AgentRuntime` 实例被复用（同一实例多次调用 `run()`），每次都会正确地切换到新的日志文件。
- 如果 agent working 目录下日志文件被手动删除，`cleanup()` 在 `logger.remove()` 时可能触发 `OSError`（文件已删，压缩操作失败），代码里有 `except OSError: pass` 静默处理。

## 新人易踩的坑

- 文件路径 `self._current_log_file` 在 `setup()` 后被设置为目录路径（`self._log_dir`），不是实际文件路径（因为 `{time}` 还没被 loguru 解析）。调用方不应该读这个属性来确定日志文件位置，只用于"是否已设置"判断。
- 测试时如果不调用 `cleanup()`，handler 会残留，后续测试的日志都会写入同一个文件，可能造成测试间干扰。
