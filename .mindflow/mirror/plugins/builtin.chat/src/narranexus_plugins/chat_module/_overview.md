---
code_dir: plugins/builtin.chat/src/narranexus_plugins/chat_module/
last_verified: 2026-08-20
---

# chat_module/ — 对话与外部接入模块

## 目录角色

ChatModule 承担两个职责：

1. **对话历史管理**：维护 Agent 与用户之间的双轨记忆（长期：当前 Narrative 的 EverMemOS 语义记忆；短期：最近 15 条跨 Narrative 消息），并在执行后把本轮对话写入持久存储。

2. **外部接入门户**：`chat_trigger.py` 实现了符合 Google A2A 协议的 HTTP API Server，是整个 Agent 系统对外暴露的唯一标准入口。

这是一个 **Narrative 级别**的 capability module——每个 Narrative 里每个用户有一个独立的 Chat 实例，存储该用户与 Agent 的对话历史。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `chat_module.py` | Module 主体：双轨记忆加载（gather）；对话历史写入（after_turn）；MCP 委托给 `_chat_mcp_tools.py` |
| `_chat_mcp_tools.py` | MCP 工具注册：`reply_owner`（唯一的用户可见输出通道）；`get_chat_history`（查询历史） |
| `chat_trigger.py` | A2A 协议 API Server：接收外部请求，调用 AgentRuntime，支持同步和 SSE 流式响应 |
| `prompts.py` | 向 Agent 解释"思考 vs 说话"核心概念和消息发送纪律 |
| `_chat_reads.py` | dialect-safe 的 `get_chat_history` 共享实现（AgentDataStore seam 的唯一真源，供 DirectStore + backend 孪生路由） |
| `_chat_writes.py` | bootstrap 问候行的唯一写入方（2026-08-20）：`build_bootstrap_greeting_row` 定义行结构 + turn 起点时间戳约束，`seed_bootstrap_greeting` 幂等落库；`persist_turn` 与 `step_1` 开局 seed 共用，见 [[_chat_writes]] |

## 和外部目录的协作

- `EventMemoryModule`（`event_memory_module/`）是 ChatModule 的存储后端：长期历史通过 `search_instance_json_format_memory` 读取，`add_instance_json_format_memory` 写入
- `MemoryModule` 把 EverMemOS 查询结果缓存在 `ctx_data.extra_data["evermemos_memories"]`，ChatModule 的 `gather` 优先读取这个缓存作为长期记忆的来源
- `repository/InstanceRepository.get_chat_instances_by_user()` 用于短期记忆加载（查询用户在其他 Narrative 里的 Chat 实例）
- `agent_runtime/AgentRuntime` 被 `chat_trigger.py` 直接调用来处理外部 A2A 请求
