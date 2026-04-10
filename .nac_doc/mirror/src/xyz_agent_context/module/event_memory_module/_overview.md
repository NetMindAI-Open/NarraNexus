---
code_dir: src/xyz_agent_context/module/event_memory_module/
last_verified: 2026-04-10
---

# event_memory_module/ — Narrative 级别记忆基础设施

## 目录角色

`EventMemoryModule` 是一个**基础设施 Module**，不直接向 Agent 提供能力，而是为其他 Module 提供 Narrative 级别的存储服务。它管理两类数据：

1. **JSON 格式记忆**（`instance_json_format_memory_{module_name}` 表）：按 `instance_id` 隔离的结构化 JSON 数据，供 `ChatModule` 存储对话历史、`SocialNetworkModule` 存储实体信息等使用
2. **报告记忆**（`module_report_memory` 表）：Module 向 Narrative 汇报的状态摘要，用于 Narrative 编排决策

这个目录**没有 MCP 服务器**，**没有系统提示**，不向 LLM 暴露任何东西。它是纯粹的存储层抽象。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `event_memory_module.py` | 存储服务：`search/add_instance_json_format_memory`、`update_report_memory` |

## 和外部目录的协作

- `ChatModule` 在 `hook_data_gathering` 里调用 `search_instance_json_format_memory` 读历史，在 `hook_after_event_execution` 里调用 `add_instance_json_format_memory` 写历史
- `SocialNetworkModule` 的 `hook_after_event_execution` 也调用这里写实体信息摘要
- `NarrativeService` 读取 `module_report_memory` 来决定哪些模块需要激活
