---
code_dir: src/xyz_agent_context/module/
last_verified: 2026-04-10
---

# module/ — 插件式能力层

## 目录角色

`module/` 是 Agent 系统的可插拔能力层。每个子目录是一个自包含的功能域——Chat、Job、SocialNetwork、RAG 等——可以独立启用或禁用而不影响其他模块。Module 是 Agent 积累持久化状态、向 LLM 暴露 MCP 工具、以及在执行后响应事件的主要方式。

## 一次请求的完整流转

1. `AgentRuntime` 调用 `ModuleService.load_modules()`，进入 `_module_impl/loader.py`
2. `ModuleLoader` 将 capability modules（规则加载）和 task modules（LLM 决策，通过 `instance_decision.py`）分开处理
3. 加载完毕后，`HookManager` 将 `hook_data_gathering` 扇出到所有已加载的 module（默认顺序执行，可选并行）
4. AgentLoop 运行结束，`HookManager` 并行扇出 `hook_after_event_execution`
5. 任何返回 `trigger_callback=True` 的 `HookCallbackResult` 会激活等待中的依赖实例

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `base.py` | 所有 Module 必须满足的抽象契约 |
| `__init__.py` | `MODULE_MAP` 注册表 + 包导出 |
| `module_service.py` | Facade：AgentRuntime 只与此交互 |
| `hook_manager.py` | 并行化并编排 hook 调用 |
| `module_runner.py` | 部署 MCP 服务器（多进程或线程）+ A2A API |
| `_module_impl/` | 私有：LLM 实例决策、上下文合并、元数据 |
| `*_module/` | 具体模块实现 |
| `job_module/job_trigger.py` | Job 后台轮询执行服务 |
| `chat_module/chat_trigger.py` | 对外 A2A 协议 API |

## 和外部目录的协作

- `agent_runtime/` 通过 `ModuleService` 加载模块；通过 `HookManager` 调用 hook
- `narrative/` 提供 `Narrative` 对象（含 `active_instances`），作为实例决策的上文
- `repository/` 被各个 module 直接引用，做数据读写
- `services/` 中的 `InstanceSyncService` 负责将 LLM 输出的 `task_key` 转换为真实 `instance_id` 并写库
- `agent_framework/` 提供 LLM 调用能力（`OpenAIAgentsSDK`），用于实例决策和 job lifecycle 分析

## Gotchas

- `MODULE_MAP` 是唯一的模块注册入口。忘记在这里注册新模块，即使类存在也永远不会被加载，且不会有任何报错。
- `ALWAYS_LOAD_MODULES`（目前只有 `SkillModule`）完全绕过实例决策，以合成的内存实例（ID 固定为 `skill_default`）注入。
- 存在"虚拟 JobModule 注入"机制：如果 LLM 决策返回零个 JobModule 实例，`loader.py` 仍会插入一个空 `instance_id` 的虚拟实例，确保 `job_create` MCP 工具可访问。
- `hook_data_gathering` 默认是**顺序执行**——改为 `parallel_data_gathering=True` 需要确保各模块写入 `ContextData` 的不同字段，否则 `ContextDataMerger` 的 last-write-wins 合并策略会静默丢弃数据。
- MCP 服务器在 SQLite 模式下运行于单进程多线程模式（避免多进程写锁争用），生产环境（MySQL/PostgreSQL）则每个模块独立进程。
