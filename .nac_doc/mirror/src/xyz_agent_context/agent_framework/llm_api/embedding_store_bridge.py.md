---
code_file: src/xyz_agent_context/agent_framework/llm_api/embedding_store_bridge.py
last_verified: 2026-04-10
stub: false
---
# embedding_store_bridge.py — 向量持久化的统一入口

## 为什么存在

在新 provider 系统引入之前，各个模块把 embedding 向量直接存在各自表的列里（如 `narratives.embedding`）。新系统引入了统一的 `embeddings_store` 表来管理所有向量，支持多模型、多 entity type。如果每个模块都直接 import `EmbeddingStoreRepository`，会导致所有模块都依赖 repository 层，且切换存储策略要改多处。这个 bridge 把"该存哪"的决策集中在一处：只有 `llm_config.json` 存在时才走新路径，否则保持旧行为。

## 上下游关系

上游调用者：所有需要持久化或读取向量的模块（`narrative` 包的向量操作、`job_module` 的语义存储等）。调用方只需 `store_embedding("narrative", nar_id, vector)` 和 `get_stored_embedding("narrative", nar_id)`，不用知道底层 `EmbeddingStoreRepository` 的存在。

下游：`EmbeddingStoreRepository`（通过 lazy import 避免循环依赖），读取 `api_config.embedding_config.model` 来识别当前模型，确保同一 entity 的不同模型向量可以共存于表中。

`use_embedding_store()` 函数通过 `provider_registry.config_exists()` 检查是否存在 `llm_config.json`，作为新/旧路径的开关。

## 设计决策

**非致命性错误**：所有函数的异常都被 `try/except` 捕获后 log warning 并返回 `None`/`{}`，而不是抛出。向量存储失败不应该中断主流程（agent 回答用户问题的流程）。这是有意识的降级策略。

**lazy import 避免循环依赖**：`_get_repo()` 在调用时才 import `EmbeddingStoreRepository` 和 `get_db_client`，而不是模块加载时 import。这避免了 `agent_framework` → `repository` → `utils` → `agent_framework` 的潜在循环。

**model 维度不固定在 EmbeddingConfig.dimensions**：读取向量时用 `embedding_config.model` 作为 key 查找，同一 entity 可以同时有多个不同 model 的向量。用户切换 embedding model 后旧向量不会被删除，只是新向量用新 model 存。

## Gotcha / 边界情况

- `use_embedding_store()` 返回 `True` 的条件是 `llm_config.json` 存在，这意味着本地单机用户（没有配置过 provider 的老用户）会一直用旧路径，即使他们实际上在读旧表列。这是有意的迁移策略。
- `store_embedding` 的 `source_text` 最多截断到 2000 字符存储，这只是辅助信息（用于未来重新 embed），不影响向量本身。

## 新人易踩的坑

- 调用 `get_stored_embedding()` 返回 `None` 可能是两种情况：embedding 不存在，或者存储读取失败。两者都被 log warning + 返回 None，调用方无法区分。需要先看日志。
- 模块直接用旧表列读向量的代码和这个 bridge 是并行存在的，不是替换关系。向量查询的入口取决于哪个代码路径先触发，确认使用的是哪个 `use_embedding_store()` 分支很重要。
