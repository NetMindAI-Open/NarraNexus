---
code_dir: src/xyz_agent_context/module/awareness_module/
last_verified: 2026-04-10
---

# awareness_module/ — Agent 自我认知模块

## 目录角色

AwarenessModule 给 Agent 提供"自我意识"——它存储 Agent 关于用户偏好、工作风格、沟通方式的长期观察（Awareness Profile），并在每次对话开始时把这个 profile 注入系统提示。同时提供两个 MCP 工具：`update_awareness`（更新 profile）和 `update_agent_name`（更新 Agent 名字）。

这是一个 **Agent 级别**的 capability module——每个 Agent 只有一个实例，跨所有 Narrative 和用户共享。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `awareness_module.py` | Module 主体：hook_data_gathering 加载 profile；MCP 服务器（port 7801）提供更新工具 |
| `prompts.py` | `AWARENESS_MODULE_INSTRUCTIONS`：向 Agent 解释 Awareness 的三个维度和更新时机 |

## 和外部目录的协作

- `repository/InstanceAwarenessRepository`（`instance_awareness` 表）负责读写 awareness 文本，AwarenessModule 是唯一的消费方
- `repository/InstanceRepository` 用于通过 `agent_id + module_class` 查找 `instance_id`（fallback 路径）
- `repository/AgentRepository` 被 MCP 工具 `update_agent_name` 直接调用
- `ctx_data.awareness` 字段由本模块在 `hook_data_gathering` 时填充，被 `prompts.py` 的 `{awareness}` 占位符消费
