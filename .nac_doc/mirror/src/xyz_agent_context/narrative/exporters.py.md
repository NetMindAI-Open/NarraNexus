---
code_file: src/xyz_agent_context/narrative/exporters.py
last_verified: 2026-04-10
stub: false
---

# exporters.py — 调试导出工具（Markdown 快照 + Trajectory 追踪）

## 为什么存在

Agent 运行时 LLM 的决策过程是不透明的——为什么某轮换了 Narrative？某个 Instance 什么时候被激活？这些决策如果只存在内存里，出了问题很难复现。`exporters.py` 提供两个工具把这些状态快照写到文件：`NarrativeMarkdownManager` 把 Narrative 的 Instance 状态和关系图渲染成可读的 Markdown；`TrajectoryRecorder` 把每轮执行的完整轨迹（决策前后状态、LLM reasoning、tool call 次数）写成 JSON。

这两个类对主流程**没有影响**——它们是只写的调试辅助，不被任何 Service 或 AgentRuntime 步骤强依赖。

## 上下游关系

**被谁用**：`agent_runtime/` 在某些调试路径下调用这两个类；前端可能通过 API 读取 Markdown 内容展示 Narrative 历史（通过 `read_markdown()`）；开发者手动分析问题时直接读取 `sessions/` 和 `trajectories/` 目录下的文件。

**依赖谁**：依赖 `xyz_agent_context.settings.settings` 获取文件存储路径（`narrative_markdown_path` 和 `trajectory_path`），路径可通过环境变量覆盖。`TrajectoryRecorder` 还依赖 `ExecutionState` 和 `ModuleInstance` 类型（通过 TYPE_CHECKING 懒导入，避免循环依赖）。

## 设计决策

文件目录结构是 `{base_path}/{agent_id}/{user_id}/narratives/` 和 `{base_path}/{agent_id}/{user_id}/trajectories/{narrative_id}/`，按 agent 和 user 两级隔离，多 agent 多用户运行时文件不会互相污染。

`NarrativeMarkdownManager._update_section()` 通过字符串扫描更新 Markdown 的特定章节，而不是替换整个文件——这样 "Change History" 章节可以追加，旧记录不丢失。代价是实现比较脆弱，依赖 Markdown 的 `## 章节标题` 格式不变。

曾考虑把 Trajectory 写入数据库，但体量可能很大（每轮包含完整的 execution_state），写文件更简单且不影响主数据库性能。

## Gotcha / 边界情况

`NarrativeMarkdownManager.initialize_markdown()` 有幂等保护——如果文件已存在不会覆盖。但 `update_instances()` 和 `update_statistics()` 会覆盖对应章节。如果并发调用这两个方法（多个 AgentRuntime 实例同时更新同一个 Narrative），文件写入没有锁保护，可能出现数据撕裂。

`TrajectoryRecorder._update_index()` 在写入前检查 `round_num` 是否已存在，避免重复记录。但如果同一轮被并发触发两次（edge case），索引去重依赖的是 round_num 相同，而不是 wall clock 时间，因此仍然安全。

## 新人易踩的坑

这两个类都需要在 `async` 环境里使用（方法都是 `async def`），但内部的文件 IO 是同步的（普通 `open()`）。在高并发场景下这会阻塞 asyncio event loop，但因为这是调试工具，当前版本接受这个权衡。如果在高频路径上启用，应该改成 `aiofiles`。
