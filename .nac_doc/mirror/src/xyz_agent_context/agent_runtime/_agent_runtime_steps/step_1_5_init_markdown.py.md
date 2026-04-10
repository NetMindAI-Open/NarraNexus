---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_1_5_init_markdown.py
last_verified: 2026-04-10
stub: false
---
# step_1_5_init_markdown.py — 流水线第 1.5 步：初始化 Markdown 历史

## 为什么存在

Step 2 的 Module 决策（LLM 决定激活哪些 Instance）需要知道当前 Narrative 中已有哪些 Instance 以及过去几轮对话发生了什么。这些信息存储在 Narrative 对应的 Markdown 文件里（`NarrativeMarkdownManager` 负责读写）。Step 1.5 在 Module 决策之前读取这个 Markdown 文件，并保存一份 Instance 快照（`previous_instances`）用于 Step 4 的 Trajectory 记录中对比前后变化。

## 上下游关系

输入：`ctx.main_narrative`（Step 1 输出的主 Narrative）、`markdown_manager`（`NarrativeMarkdownManager` 实例）。

输出到 RunContext：`ctx.markdown_history`（Markdown 文件全文，传给 Step 2 的 `load_modules()` 调用）、`ctx.previous_instances`（deep copy 的当前 Instance 快照）。

这是流水线中唯一不产出 `ProgressMessage` 的步骤——它静默执行，没有 Running/Completed 进度通知，不影响前端 sidebar 展示。

## 设计决策

**Deep copy `previous_instances`**：Step 2 的 Module 决策会修改 `main_narrative.active_instances`（如果有新 Instance 激活或旧的移除）。如果不 deep copy，`previous_instances` 和 `active_instances` 会指向同一个列表，Step 4 里比对"变更了什么"时会一直相同。

**步骤不生成 ProgressMessage**：Markdown 初始化是快速的文件 I/O，不涉及 LLM 调用，对用户体验影响极小。给它加进度通知会增加前端 sidebar 的噪音，且这个步骤几乎不会失败。

**新 Narrative 时 `ctx.markdown_history` 为空字符串**：`markdown_manager.initialize_markdown()` 会创建空文件，`read_markdown()` 返回空字符串。Step 2 的 Module 决策处理空 history 是正常路径（第一次对话）。

## Gotcha / 边界情况

- 如果 `ctx.main_narrative` 是 None（极端情况：Step 1 没有选出任何 Narrative），整个步骤会 skip（`if main_narrative:` 分支）。这种情况下 `ctx.markdown_history` 保持空字符串，`ctx.previous_instances` 保持空列表。
- Markdown 文件可能因为磁盘空间不足或权限问题无法读写，这里没有异常处理，错误会向上传播并中断整个 run()。

## 新人易踩的坑

- `ctx.substeps_1_5` 被追加了调试信息，但由于这个步骤不产出 ProgressMessage，这些 substeps 永远不会被前端看到，只在日志里。如果要展示 Step 1.5 的信息，需要在这里加 yield ProgressMessage。
