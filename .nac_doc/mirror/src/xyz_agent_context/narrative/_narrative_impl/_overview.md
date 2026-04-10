---
code_dir: src/xyz_agent_context/narrative/_narrative_impl/
last_verified: 2026-04-10
stub: false
---

# _narrative_impl/ — Narrative 服务的私有实现层

## 目录角色

这是 `narrative/` 包的内部引擎室，不对外导出。所有外部调用都经过 `NarrativeService` 门面，`_narrative_impl/` 的类不能被包外代码直接实例化（名称前缀 `_` 就是这个约定）。

八个文件各司其职：向量存储、数据库 CRUD、检索逻辑、LLM 更新、embedding 更新、默认 Narrative 管理、Instance 依赖处理、Prompt 构建。这种细粒度切分是为了让每个文件足够专注，可以独立修改而不影响其他部分。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `crud.py` | Narrative 的数据库读写，不含业务逻辑 |
| `vector_store.py` | 内存中的向量索引，加速 cosine 相似度计算 |
| `retrieval.py` | 向量检索 + LLM judge 确认 + EverMemOS 集成；决定"属于哪条线" |
| `updater.py` | Event 发生后更新 Narrative 的摘要、keywords、embedding |
| `continuity.py` | 判断当前 query 是否属于 session 里的现有 Narrative |
| `instance_handler.py` | Instance 完成时处理依赖链，激活 blocked Instance |
| `default_narratives.py` | 系统预置的 8 个默认 Narrative 的定义和初始化逻辑 |
| `prompt_builder.py` | 把 Narrative 序列化成 LLM prompt 片段 |
| `prompts.py` | LLM 调用的静态 prompt 模板 |
| `_retrieval_llm.py` | retrieval.py 里 LLM judge 的具体实现（unified match、confirm 等） |

## 和外部目录的协作

**向上暴露**：通过 `_narrative_impl/__init__.py` 导出 `NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`、`PromptBuilder`、`ContinuityDetector`，供 `NarrativeService` 消费。

**外部依赖**：
- `retrieval.py` 依赖 `utils/evermemos.py` 的 `get_evermemos_client()`，以及 `_retrieval_llm.py` 里的 LLM judge 函数
- `updater.py` 依赖 `xyz_agent_context/config.py` 的 `NARRATIVE_LLM_UPDATE_INTERVAL`（全局 config）和 `agent_framework/llm_api/embedding.py`
- `continuity.py` 依赖 `agent_framework/openai_agents_sdk.OpenAIAgentsSDK` 做结构化 LLM 调用，并与 `channel/channel_context_builder_base.py` 的 Matrix 模板格式有隐式耦合（`_extract_core_content()` 函数）
- `instance_handler.py` 被 `services/module_poller.py` 直接从 `narrative` 包导入使用
