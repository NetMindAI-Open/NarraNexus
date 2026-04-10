---
code_dir: src/xyz_agent_context/module/memory_module/
last_verified: 2026-04-10
---

# memory_module/ — EverMemOS 语义记忆管理

## 目录角色

MemoryModule 是整个记忆系统的**唯一外部接口**。它把对 EverMemOS（外部向量记忆服务）的读写封装成 Module 的 hook 接口，向 Agent 暴露"语义相关的历史对话片段"而不是原始的时间序列对话记录。

MemoryModule 在 `MODULE_MAP` 里排第一位，因为它需要在 `hook_data_gathering` 的顺序执行中**最先**把 EverMemOS 查询结果缓存到 `ctx_data.extra_data["evermemos_memories"]`，后续的 `ChatModule` 才能读取这个缓存。

**Instance 模型**：MemoryModule 被加载为 capability module，无 MCP 服务器。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `memory_module.py` | 同时扮演 Module（hook）和 Service（被 `NarrativeService` 直接调用的 `search_evermemos()`、`write_to_evermemos()`） |

## 和外部目录的协作

- `utils/evermemos.EverMemOSClient` 是实际的 EverMemOS HTTP 客户端，所有读写操作委托给它
- `NarrativeService`（`narrative/`）在第 1 步（Narrative 查询阶段）调用 `MemoryModule.search_evermemos()`，把查询结果放入 `Narrative` 对象
- `AgentRuntime` 在 `hook_data_gathering` 调用链里触发 `MemoryModule.hook_data_gathering()`，从缓存中提取语义记忆注入 `ctx_data.extra_data["evermemos_memories"]`
- `ChatModule` 的 `hook_data_gathering` 读取 `ctx_data.extra_data["evermemos_memories"]` 作为长期记忆来源（优先级高于 DB 事件记忆）
