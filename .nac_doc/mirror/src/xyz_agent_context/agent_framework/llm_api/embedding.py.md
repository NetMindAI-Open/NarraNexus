---
code_file: src/xyz_agent_context/agent_framework/llm_api/embedding.py
last_verified: 2026-04-10
stub: false
---
# embedding.py — OpenAI-compatible 文本向量化客户端

## 为什么存在

Narrative 选择、Job 语义检索、实体相似度匹配等功能都需要把文本转换为向量。这个文件提供统一的 `EmbeddingClient` 类和便捷函数 `get_embedding()`，内置 in-memory 缓存、批量处理、失败重试，让各模块不用直接接触 OpenAI SDK 细节。它还包含 `cosine_similarity`、`compute_average_embedding` 等向量计算工具。

## 上下游关系

消费者分布在整个系统中：`narrative` 包（向量匹配 Narrative）、`job_module`（语义检索 Job）、`social_network_module`（实体相似度）等都通过 `get_embedding()` 或 `EmbeddingClient` 使用。

配置读自 `api_config.embedding_config`（ContextVar proxy），这意味着每个 asyncio task 使用各自 owner 的 API key。但注意 `EmbeddingClient` 实例初始化时会读取配置值并固定到 `AsyncOpenAI` 客户端里——这就是为什么每次 `get_embedding()` 调用都要创建新客户端（`_make_client()`），不能用全局缓存的 `EmbeddingClient`。

`embedding_store_bridge.py` 是它的伴随文件，负责把生成的向量持久化到 `embeddings_store` 表。

## 设计决策

**每次调用创建新 `EmbeddingClient`**：之前有全局缓存客户端，导致 `set_user_config()` 切换 ContextVar 后，已经缓存的客户端仍使用旧 API key。现在 `_make_client()` 直接 `return EmbeddingClient()`，每次读取最新 ContextVar 值。`AsyncOpenAI` 客户端创建代价低（只是初始化 HTTP client），性能可接受。`reset_global_client()` 保留为 no-op 向后兼容。

**不传 `dimensions` 参数给 API**：`EmbeddingConfig.dimensions` 只用于 UI 展示和存储预估，真正的请求不带该参数，避免切换模型时 400 错误（不同模型原生维度不同，API 会拒绝非原生维度）。

**in-memory 缓存只在单次 `EmbeddingClient` 实例生命周期内有效**：由于每次 `get_embedding()` 都创建新实例，模块级别的重复查询不走缓存。如果某个场景需要批量缓存应直接使用 `EmbeddingClient` 实例并复用。

## Gotcha / 边界情况

- `prepare_job_text_for_embedding()` 把 Job 的 title/description/payload 合并后截断到 500 字——这个截断策略对非常长的 payload 可能损失语义。这是性能和精度的权衡，不是 bug。
- `with_retry` 装饰器只重试 `ConnectionError`、`TimeoutError`、`OSError`，不重试 API 认证错误（`AuthenticationError` 等），认证失败会直接抛出。
- `embed_batch` 在 batch 结果映射回原始位置时如果有缓存 hit，`results` 数组会有 `None` 空洞，最后用 `[r for r in results if r is not None]` 过滤，如果某个位置的 embed 失败了也会被静默跳过，导致返回列表长度与输入不等。

## 新人易踩的坑

- 不要在模块加载时（如类属性）调用 `get_embedding()`，此时 ContextVar 未设置，会用全局 `_holder` 的配置，可能指向错误的 API key 或 model。
- `cosine_similarity` 和 `compute_average_embedding` 在 numpy 不可用时有纯 Python 实现，但纯 Python 版在大向量下非常慢。正式环境 numpy 是依赖，测试环境可能触发纯 Python 路径。
