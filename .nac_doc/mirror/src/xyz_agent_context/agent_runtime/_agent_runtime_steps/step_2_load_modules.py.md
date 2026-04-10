---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_2_load_modules.py
last_verified: 2026-04-10
stub: false
---
# step_2_load_modules.py — 流水线第 2 步：Module 决策与加载

## 为什么存在

系统支持多个可热插拔的 Module（SocialNetwork、Job、Chat、Awareness 等），每次对话不一定都需要所有模块。这一步通过 LLM 决策（`ModuleService.load_modules()`）判断当前对话需要哪些 Module Instance，以及执行路径是走完整 Agent Loop 还是直接触发某个工具（DIRECT_TRIGGER）。Module 决策是整个执行路径的分叉点。

## 上下游关系

输入：`ctx.narrative_list`（当前 Narrative 和其 active_instances）、`ctx.input_content`、`ctx.markdown_history`（历史 Instance 状态）、`ctx.awareness`。

输出到 RunContext：`ctx.load_result`（`ModuleLoadResult`，包含 `active_instances`、`execution_type`、`relationship_graph`、`decision_reasoning` 等）、`ctx.module_list`（含 MemoryModule 追加后的完整模块列表，用于 Step 5 hook 执行）。

`ctx.load_result.llm_error` 非 None 时说明决策时 LLM 调用失败，系统使用了 fallback（保持上次状态），同时 yield 一个 `ErrorMessage` 给前端提示。

MemoryModule 在这里追加到 `ctx.module_list`——它是 agent 级别的全局模块，不通过 Instance 机制管理，但需要参与 Step 5 的 hook 执行。

## 设计决策

**LLM 决策失败时降级而非中断**：`ModuleLoadResult.llm_error` 不为 None 时，说明 LLM 调用失败，系统使用了 fallback（通常是保持上次 Narrative 的 active_instances 不变）。流水线继续执行，用户能看到 ErrorMessage 提示但对话不中断。这是有意识的降级策略——Module 决策失败不应该让用户完全没有回复。

**执行路径决策在 Step 2 而非 Step 3**：Step 3 只是执行 Step 2 已经做出的路径决定。这样 Step 3 的两个实现（agent_loop 和 direct_trigger）保持独立，也让 Step 2.5 的 Instance 同步在执行前完成，确保 MCP 服务器在 Step 3 开始时已就绪。

## Gotcha / 边界情况

- `ctx.module_list` 和 `ctx.active_instances` 的内容不对应：`active_instances` 来自 `load_result.active_instances`，`module_list` 是 `active_instances` 中有 `.module` 的 instance 的 module 属性提取 + MemoryModule。MemoryModule 在 `module_list` 里但不在 `active_instances` 里。
- `working_source` 被转换为字符串值传给 `load_modules()`，因为 `ModuleService` 不直接依赖 `WorkingSource` enum。

## 新人易踩的坑

- `load_result.llm_error` 非 None 时，fallback 的 active_instances 内容是什么取决于 `ModuleService._fallback_load()` 的实现（在 `_module_impl/` 里），Step 2 的代码不控制这个细节。
- `load_result.raw_instances` 字段（在 Step 2.5 里用于 Job 创建）不在这里被使用，只是透传。新人查这里的代码找不到 raw_instances 的来源，要去 `ModuleService.load_modules()` 里找。
