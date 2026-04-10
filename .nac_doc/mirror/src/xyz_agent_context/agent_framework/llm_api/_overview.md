---
code_dir: src/xyz_agent_context/agent_framework/llm_api/
last_verified: 2026-04-10
stub: false
---
# llm_api/ — 底层向量化工具包

## 目录角色

这个目录是 agent_framework 中专门处理 embedding（文本向量化）相关功能的子模块。与上级目录中各个 LLM SDK 适配层（Claude、OpenAI、Gemini）并列，但专注于 embedding 这一特定能力而非对话生成。

## 关键文件索引

- **`embedding.py`**：`EmbeddingClient` 类和 `get_embedding()` 便捷函数，直接调用 OpenAI-compatible embedding API，内置缓存、批量处理和重试
- **`embedding_store_bridge.py`**：把生成的向量持久化到 `embeddings_store` 表的 bridge，同时提供读取接口；包含新旧路径切换逻辑

## 和外部目录的协作

- 这个目录向上依赖 `api_config.py`（获取 embedding 配置和 ContextVar proxy）
- 向下依赖 `repository/embedding_store_repository.py`（持久化，通过 `embedding_store_bridge.py` lazy import）
- 被整个系统的各功能模块消费，包括 `narrative/`（Narrative 向量匹配）、`module/job_module/`（语义 Job 检索）、`module/social_network_module/`（实体相似度）
- `embedding.py` 中的向量计算工具（`cosine_similarity`、`compute_average_embedding`）也被 `narrative/` 包直接使用

这个目录故意不包含对话 LLM 相关代码（那些在上级目录的各 `*_sdk.py` 文件里），保持单一职责。
