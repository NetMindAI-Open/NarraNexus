---
code_dir: src/xyz_agent_context/narrative/
last_verified: 2026-04-10
stub: false
---

# narrative/ — 系统的记忆核心

## 目录角色

`narrative/` 是整个 NexusAgent 最核心的模块，负责"长期记忆"的全生命周期管理。它解决了一个根本问题：面对开放式的多轮对话，Agent 需要知道"当前这句话属于哪一条故事线"，然后在那条线上持续积累上下文。

这个包以 **服务协议层** 的方式对外暴露三个公共 Service（NarrativeService、EventService、SessionService），所有私有实现都深藏在 `_narrative_impl/` 和 `_event_impl/` 两个子目录里。`models.py` 是唯一横贯所有层的公共数据类型文件。

它不是"聊天历史"的简单容器。每一个 Narrative 是一条有名字、有关键词、有向量 embedding 的"主题线索"，系统在每次对话时动态判断应该把这次输入挂在哪条线上，再把该线上的历史事件注入 LLM 上下文。

## 关键文件索引

| 文件 | 职责简述 |
|------|----------|
| `models.py` | 所有数据模型的唯一来源：Event、Narrative、ConversationSession 等 |
| `narrative_service.py` | 对 AgentRuntime 暴露的门面；协调 select / update / CRUD |
| `event_service.py` | Event 创建、更新、上下文筛选的门面 |
| `session_service.py` | 基于文件的 Session 持久化；维护用户与 Narrative 的绑定关系 |
| `config.py` | 所有可调参数的单一配置文件（阈值、模型名、timeout 等） |
| `exporters.py` | 调试用的 Markdown / Trajectory 导出工具，不影响主流程 |
| `_narrative_impl/` | 私有实现：向量检索、LLM 更新、默认 Narrative、连续性检测 |
| `_event_impl/` | 私有实现：Event CRUD、embedding 生成、上下文筛选 |

## 和外部目录的协作

**被谁调用**：主要被 `agent_runtime/_agent_runtime_steps/` 调用（step_1 调 select、step_5 调 update_with_event）；`services/instance_sync_service.py` 在写 Job 时会写入 Narrative actors；`services/module_poller.py` 在 Instance 完成时调 `InstanceHandler.handle_completion`。

**依赖谁**：依赖 `agent_framework/llm_api/embedding.py` 生成向量；依赖 `repository/narrative_repository.py` 做数据库读写；依赖 `schema/module_schema.py` 的 ModuleInstance 类型；`continuity.py` 依赖 `agent_framework/openai_agents_sdk` 做 LLM 结构化调用。

**channel 模块的耦合**：`_narrative_impl/continuity.py` 里的 `_extract_core_content()` 硬编码了 Matrix 消息模板的格式（`[Matrix · ... · ...] ...`），用于剥离 channel wrapper 取出核心消息内容。如果 `channel/channel_prompts.py` 或 `channel_context_builder_base.py` 改变了模板格式，这里必须同步更新。
