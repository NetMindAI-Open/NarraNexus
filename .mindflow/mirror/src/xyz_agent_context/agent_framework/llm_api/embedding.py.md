---
code_file: src/xyz_agent_context/agent_framework/llm_api/embedding.py
last_verified: 2026-04-20
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

**`self.dimensions` 只用于日志**：构造时用 `model_catalog.get_embedding_dimensions(model)`
作为权威真源（覆盖 bge-m3 1024d、nv-embed-v2 4096d 等），fallback 到文件内
`MODEL_DIMENSIONS` dict（仅 OpenAI 三款），再 fallback 到平台默认 1536。实际存储
用 `len(vector)` 动态读真实维度，这里的 `self.dimensions` 只用于初始化日志
`[Embedding] Initialized: model=..., dims=...`。2026-04-20 之前 fallback 写 1536
导致 bge-m3 场景日志显示 1536（cosmetic 错误，不影响存储/检索）。

**in-memory 缓存只在单次 `EmbeddingClient` 实例生命周期内有效**：由于每次 `get_embedding()` 都创建新实例，模块级别的重复查询不走缓存。如果某个场景需要批量缓存应直接使用 `EmbeddingClient` 实例并复用。

## Gotcha / 边界情况

- `prepare_job_text_for_embedding()` 把 Job 的 title/description/payload **全量**合并（2026-06-01 起去掉了 `payload[:500]` 截断——prod 实测 title+desc+payload 全量最长 ~2672 token，远低于 8000-token 守卫；旧的 500 字截断砍掉了 65% job 的执行指令，损失了区分度最高的语义）。
- **token 守卫（2026-06-01）**：`_make_embedding_request` / `_make_batch_embedding_request` 调 API 前用 `_truncate_to_token_budget`（tiktoken cl100k 作 provider-agnostic 保守上界）按 token 截到 `MAX_TOKENS_PER_REQUEST`（8000）。`MAX_TOKENS_PER_REQUEST` 语义是 **token 不是字符**。单条路径还有 reactive 兜底：真 400（`_is_context_length_error`）时截半重试一次，覆盖 bge-m3 等 tokenizer 与 cl100k 的差异。设计见 reference/self_notebook/specs/2026-06-01-embedding-anchor-redesign-design.md。
- `with_retry` 装饰器只重试 `ConnectionError`、`TimeoutError`、`OSError`，不重试 API 认证错误（`AuthenticationError` 等），认证失败会直接抛出。
- `embed_batch` 在 batch 结果映射回原始位置时如果有缓存 hit，`results` 数组会有 `None` 空洞，最后用 `[r for r in results if r is not None]` 过滤，如果某个位置的 embed 失败了也会被静默跳过，导致返回列表长度与输入不等。

## 新人易踩的坑

- 不要在模块加载时（如类属性）调用 `get_embedding()`，此时 ContextVar 未设置，会用全局 `_holder` 的配置，可能指向错误的 API key 或 model。
- `cosine_similarity` 和 `compute_average_embedding` 在 numpy 不可用时有纯 Python 实现，但纯 Python 版在大向量下非常慢。正式环境 numpy 是依赖，测试环境可能触发纯 Python 路径。
