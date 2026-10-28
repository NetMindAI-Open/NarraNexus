---
code_dir: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/
last_verified: 2026-04-10
---

# social_network_module/ — Agent 的社交图谱记忆层

## 目录角色

SocialNetworkModule 让 Agent 具备"认识人"的能力——记录与之交互的用户、Agent、组织的身份信息、标签和联系方式，并在每次对话时自动加载当前用户的档案，让 Agent 能做到个性化回复和关系推理。

它是 Agent-level module（每个 Agent 一个实例，所有 Narrative 共享），端口 7802。

两条工作模式并行运行：
- **MCP 工具**：Agent 主动调用 `extract_entity_info` 结构化录入信息，调用 `search_social_network` 检索联系人
- **自动更新 hook**：每次对话结束后，`after_turn` 自动提炼会话新信息追加到 `entity_description`，并通过 `_entity_updater.py` 更新向量和 Persona

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `social_network_module.py` | Module 主体：hook 生命周期；对外公开 API（供 MCP Server 调用）；模糊实体匹配 |
| `_social_mcp_tools.py` | MCP 工具注册：`extract_entity_info`、`search_social_network`、`get_contact_info`、`get_agent_social_stats` |
| `_entity_updater.py` | LLM 驱动的实体更新管道：会话摘要、描述追加、向量更新、Persona 推断、批量实体提取 |
| `prompts.py` | 系统指令模板（`SOCIAL_NETWORK_MODULE_INSTRUCTIONS`）；四个 LLM 更新用提示词（摘要、压缩、Persona 推断、批量提取） |

> 2026-08-17：删掉了本表里 `test_persona.py` 一行。那是个手工 demo 脚本
> （print + 需要真 LLM key + 真 DB + `__main__` 驱动），却叫 `test_*.py`：
> `make test` 只跑 `tests/` 所以它从不执行，裸跑 `pytest` 会收集到并失败，
> 而它断言的 `_should_update_persona` 早已重构进 `_entity_updater`，只能永远
> 失败。纯逻辑部分移到
> `tests/social_network_module/test_persona_refresh.py`。

## 和外部目录的协作

- `repository/SocialNetworkRepository`：唯一的实体 DB 操作通道，支持精确查询、标签搜索、语义向量检索、关键词模糊搜索
- `JobModule`（跨模块数据传递）：`gather` 把当前实体的 `related_job_ids` 写入 `ctx_data.extra_data`，JobModule 在后续的顺序 hook 里读取，加载关联 Job 上下文
- `job_service.JobInstanceService`：创建 Job 时调用 `_sync_job_to_entity()` 把 `job_id` 写回 Entity，形成双向索引
- `agent_framework/llm/api/embedding`：`_entity_updater.update_entity_embedding()` 更新实体向量；语义检索时也需要嵌入 API
