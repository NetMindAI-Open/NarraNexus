---
code_file: src/xyz_agent_context/module/social_network_module/prompts.py
last_verified: 2026-04-10
---

# prompts.py — SocialNetworkModule 提示词集合

## 为什么存在

集中管理 SocialNetworkModule 所有用到的提示词字符串，避免散落在 `social_network_module.py` 和 `_entity_updater.py` 里。分两类：一是 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS`，注入到系统提示里告诉 Agent 如何使用社交网络功能；二是四个 LLM 操作提示词（`ENTITY_SUMMARY_INSTRUCTIONS`、`DESCRIPTION_COMPRESSION_INSTRUCTIONS`、`PERSONA_INFERENCE_INSTRUCTIONS`、`BATCH_ENTITY_EXTRACTION_INSTRUCTIONS`），由 `_entity_updater.py` 的各个函数调用。

## 上下游关系

- **被谁用**：`SocialNetworkModule.__init__()` 引用 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 初始化 `self.instructions`；`_entity_updater.py` 里的各个 LLM 调用函数各自引用对应的操作提示词
- **依赖谁**：无（纯字符串常量）

## 设计决策

**`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 的核心约束**：指令里有大量关于"何时记录"（立即，不要等待或请求许可）、"标签纪律"（少而精，用规范形式）、"实体 ID 规则"（当前用户用 `user_id`，其他人用 `entity_{name}_{timestamp}`）的规范性说明。这些约束直接决定了 Agent 的社交记忆质量——如果 Agent 不遵守标签纪律，几周后每个实体就会积累几十个无意义的标签。

**`{agent_id}` 占位符**：`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里有 `{agent_id}` 占位符，在 `SocialNetworkModule.__init__()` 里用 `.replace("{agent_id}", agent_id)` 替换，而不是通过 Python f-string 或 `.format()`。这是因为指令里可能有其他花括号（如示例代码或 JSON 格式），用 `.replace()` 只替换指定变量，不会意外处理其他花括号。

**`{social_network_current_entity}` 没有在这里定义**：这个占位符出现在 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里，但它是运行时由 `hook_data_gathering` 通过 `ctx_data.social_network_current_entity` 注入、再由 `get_instructions()` 格式化填充的。阅读代码时注意这个两步替换机制。

## Gotcha / 边界情况

- **`BATCH_ENTITY_EXTRACTION_INSTRUCTIONS` 要求 LLM 不提取主发言人本身**：批量提取提示词里明确说"不包含正在交互的主要用户（primary speaker）"。如果 Agent 提取了主用户自己（`entity_id` 和 `user_id` 相同），会在 `hook_after_event_execution` 里被 `extract_mentioned_entities()` 过滤掉（通过 `primary_entity_name` 参数排除）。

## 新人易踩的坑

- 修改 `PERSONA_INFERENCE_INSTRUCTIONS` 时要确保要求 LLM 返回的字段与 `_entity_updater.PersonaOutput` Pydantic 模型一致（目前只有一个字段 `persona: str`）。如果在提示词里要求更多返回字段但忘记更新 Pydantic 模型，LLM 会返回额外内容但会被丢弃。
