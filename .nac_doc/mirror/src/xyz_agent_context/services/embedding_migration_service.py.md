---
code_file: src/xyz_agent_context/services/embedding_migration_service.py
last_verified: 2026-04-10
stub: false
---

# embedding_migration_service.py — Embedding 模型切换后的向量重建工具

## 为什么存在

系统里有四类实体（Narrative、Event、Job、SocialEntity）各自存储了 embedding 向量用于语义检索。当用户在设置里切换 embedding 模型（比如从 `text-embedding-3-small` 换到 `text-embedding-3-large`），历史向量和新模型的向量处于不同的向量空间，混用会导致语义检索完全失效（召回结果完全随机）。`EmbeddingMigrationService` 扫描所有实体、跳过已有新模型向量的记录、为缺失的记录生成并存储新向量，支持进度查询和断点续跑。

## 上下游关系

**被谁用**：`backend/routes/` 里有专门的 API 端点（`/api/embedding/status` 和 `/api/embedding/rebuild`），前端在设置页面切换模型后会提示用户触发迁移，也可以查看进度。

**调用谁**：`repository/embedding_store_repository.EmbeddingStoreRepository` 做向量的批量写入和存在性检查；`agent_framework/llm_api/embedding.get_embedding()` 生成向量；`agent_framework/api_config.embedding_config` 读取当前配置的模型名；直接用原始 SQL 查询四张业务表（`narratives`、`events`、`instance_jobs`、`instance_social_entities`）。

## 设计决策

模块级全局单例 `_progress = MigrationProgress()` 记录当前迁移状态，API 的 `get_status()` 直接读这个单例。这意味着同一个进程里只能跑一次迁移，并发调用 `rebuild_all()` 会直接返回（通过 `is_running` 标志检查）。

每类实体的 `_source_text_builder` 函数需要与原始 embedding 生成逻辑完全对齐——这是设计中最脆弱的部分。每个 builder 函数的 docstring 里有交叉引用注释指向原始生成路径，如果那边逻辑改了，这边必须同步修改，否则重建出的向量和历史向量的语义会不一致（但不会崩溃，只会悄悄降低检索质量）。

`get_status()` 和 `_rebuild_*()` 里的 SQL WHERE 条件故意保持一致（通过共享的 `_EVENT_WHERE` 等常量），防止"总数"和"实际处理数"不匹配导致进度永远停在"还差 1 个"。

数据清理（`_cleanup_before_rebuild()`）在迁移开始前和 `get_status()` 前都会运行，清除维度为 0 的哨兵记录和没有任何文字内容的空壳 Entity。

## Gotcha / 边界情况

`legacy_mode` 判断：如果系统没有配置 `llm_config.json`（即没有使用独立的 embedding 存储表），`use_embedding_store()` 返回 False，`get_status()` 会直接返回 `all_done: True` 且 `legacy_mode: True`，不做任何实际检查。这是为了兼容只用 Narrative 表原生向量列的旧部署模式。

每批（batch_size=20）处理完后有 `asyncio.sleep(0.1)` 的小延迟，目的是避免对 embedding API 的 rate limiting。如果 API 配额宽裕（比如 Azure OpenAI 或本地模型），可以减小这个值加快迁移速度。

`_rebuild_narratives()` 用了 `JSON_UNQUOTE(JSON_EXTRACT(...))` 的 MySQL 语法来从 JSON 列里提取 `narrative_info.name`——这在 SQLite 里语法不同。如果在 SQLite 模式运行迁移，这个函数会报错。需要检查当前用的是哪个后端。

## 新人易踩的坑

迁移进度是进程内状态，重启服务后进度归零，但迁移实际上支持断点续跑（跳过已有向量的记录），重新触发 `rebuild_all()` 只会处理剩余的记录，不会重复处理。进度显示从 0 开始是 UI 层的缺陷，实际工作量是正确的。

`EmbeddingMigrationService` 本身不是后台常驻进程，每次请求都是一次性的扫描任务。触发后它会在同一个 HTTP 请求里异步跑完（或在超时前尽量跑），不需要 Celery 或独立工作线程——但这意味着如果数据量很大（几万条记录），HTTP 请求可能超时，需要在调用时设置合理的客户端超时。
