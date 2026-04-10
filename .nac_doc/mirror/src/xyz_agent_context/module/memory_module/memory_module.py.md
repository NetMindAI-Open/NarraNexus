---
code_file: src/xyz_agent_context/module/memory_module/memory_module.py
last_verified: 2026-04-10
---

# memory_module.py — MemoryModule 实现

## 为什么存在

MemoryModule 承担双重角色：作为 Module 通过 hook 机制与 AgentRuntime 集成，同时作为 Service 被 `NarrativeService` 直接调用。这是系统里少有的"既是 Module 又是 Service"的组件，存在是因为记忆读写逻辑与 Narrative 生命周期深度绑定，单独拆分会导致循环依赖。

**Hook 实现**：
- `hook_data_gathering`：从 NarrativeService 预置的 EverMemOS 缓存里提取语义记忆注入 `ctx_data.extra_data["evermemos_memories"]`
- `hook_after_event_execution`：把当前轮次的对话写入 EverMemOS

**没有 MCP 服务器**：Agent 不直接调用 EverMemOS，记忆访问是系统自动管理的。

## 上下游关系

- **被谁用**：`ModuleLoader`（hook 路径）；`NarrativeService`（直接调用 `search_evermemos()` 和 `write_to_evermemos()`）；`get_memory_module()` 工厂函数（模块级缓存单例）
- **依赖谁**：`utils/evermemos.EverMemOSClient`；`narrative/config.py` 提供 `top_k` 等配置参数；`ContextData`（读写 `extra_data["evermemos_memories"]`）

## 设计决策

**全局实例缓存（`_memory_modules` dict）**：`get_memory_module(agent_id, user_id)` 以 `f"{agent_id}_{user_id}"` 为键缓存实例。这是为了避免 `NarrativeService` 每次调用都重新创建实例（EverMemOSClient 的初始化有开销）。代价是进程级别的状态保留，测试时需要清理。

**写入采用 `asyncio.create_task` 异步化**：`hook_after_event_execution` 里把写 EverMemOS 的操作创建为后台 task，不阻塞当前轮次的响应返回。这是有意的性能优化，代价是写入失败不会影响当前轮次（静默失败）。

**从 NarrativeService 缓存读取而不是实时查询**：`hook_data_gathering` 读取的是 `NarrativeService` 在第 1 步查询时放入 `ctx_data.extra_data["evermemos_memories"]` 的缓存，而不是实时查 EverMemOS。这避免了 hook 阶段的重复查询，但意味着如果 NarrativeService 没有预填缓存，这里会什么都不做。

## Gotcha / 边界情况

- **EverMemOS 不可用时的降级**：如果 `EverMemOSClient` 初始化失败或查询报错，`hook_data_gathering` 会静默跳过，`ctx_data.extra_data["evermemos_memories"]` 不会被填充。下游的 `ChatModule` 会 fallback 到 DB 事件记忆，不会崩溃。
- **`write_to_evermemos` 的 fire-and-forget**：写入错误只会打 warning 日志，不会抛出异常。如果 EverMemOS 持续失败，语义记忆会慢慢过时但系统不会崩溃。

## 新人易踩的坑

- 混淆 MemoryModule 和 EventMemoryModule：前者管理 EverMemOS 语义压缩记忆（高质量、语义检索），后者管理原始 JSON 事件记忆（全量、按实例隔离）。两者互补而非竞争，ChatModule 同时使用两者（优先 EverMemOS）。
