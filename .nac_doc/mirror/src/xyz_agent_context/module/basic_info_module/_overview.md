---
code_dir: src/xyz_agent_context/module/basic_info_module/
last_verified: 2026-04-10
---

# basic_info_module/ — 基础信息模块

## 目录角色

BasicInfoModule 是最简单的 capability module。它不做数据收集（`hook_data_gathering` 是空实现），没有 MCP 服务器，只向 Agent 的系统提示里注入静态基础信息：当前时间、Agent ID、用户 ID、系统环境说明等。

这是 Agent 了解自身身份和运行环境的基础通道，类似于"自我介绍"层。

**Instance 模型**：Agent 级别，每个 Agent 一个实例，通过 `InstanceFactory` 自动创建。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `basic_info_module.py` | Module 主体：`get_mcp_config()` 返回 `None`，不提供任何工具 |
| `prompts.py` | `BASIC_INFO_MODULE_INSTRUCTIONS`：注入 agent_id、user_id、当前时间等环境信息 |

## 和外部目录的协作

BasicInfoModule 几乎与外部目录没有直接依赖。它的 `prompts.py` 里的占位符（如 `{agent_id}`、`{user_id}`）由 `XYZBaseModule.get_instructions()` 用 `ctx_data` 字段填充。
