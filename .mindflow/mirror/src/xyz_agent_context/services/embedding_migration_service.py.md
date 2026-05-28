---
code_file: src/xyz_agent_context/services/embedding_migration_service.py
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — `entity` 类型从迁移服务下架（Owner spec, scope B）

`_rebuild_entities`、`_entity_count_sql`、`_entity_source_text`、
`_ENTITY_TEXT_FILTER` 全删了；`_status_queries` 不再返回 `entity`
行；`_user_entity_ids("entity")` 分支删除；`_cleanup_before_rebuild` 的
迭代器从 4 元缩到 3 元 (`narrative / event / job`)；`rebuild_all` 不
再调 `_rebuild_entities`。

理由：[[social_network_module.py]] 的 semantic-search 链路整段下线，
SocialEntity 的 embedding 既无写也无读，再做迁移没意义。
`embeddings_store` 里残留的 `entity_type='entity'` 行处于 dormant 状态
（保留不删，符合铁律 #6）。

# embedding_migration_service.py — Per-user embedding 向量重建工具

## 为什么存在

三类实体（Narrative / Event / Job）各自在 `embeddings_store`
里按 `(entity_type, entity_id, model)` 维度保存向量。用户切换 embedding
模型时，历史向量和新模型向量处于不同向量空间，混用会让语义检索失效。
（2026-05-27 SocialEntity 不在此列——见上方迁移说明。）

多租户云端场景里，每个用户的 provider 配置独立，切换时机也各自错开：本服务必须按用户
分片，扫描并只为该用户的实体生成缺失向量。桌面单用户场景同样走这条路径，只是它那
个用户名通常固定。

## 上下游关系

**被谁用**：`backend/routes/providers.py` 的两个端点
`GET /api/providers/embeddings/status?user_id=...`、
`POST /api/providers/embeddings/rebuild?user_id=...`；前端
`stores/embeddingStore.ts` 根据 `useConfigStore.userId` 传当前登录用户。

**调用谁**：`repository/embedding_store_repository.EmbeddingStoreRepository`
做向量的批量写入 / 存在性检查；`agent_framework/llm_api/embedding.get_embedding()`
生成向量；`agent_framework/api_config.get_user_llm_configs(user_id)` 解析当前
用户的 embedding 模型；直接用原始 SQL 查询四张业务表（按 user_id 过滤，narratives
通过 JOIN `agents.created_by`、entities 通过 JOIN `module_instances.user_id`）。

## 设计决策

**per-user 进度隔离**：`_progress_by_user: Dict[str, MigrationProgress]`
替代早期的全局单例，`get_migration_progress(user_id)` 按用户隔离。用户 A 的
rebuild 正在跑时不会影响用户 B 的状态查询。测试辅助函数
`_reset_progress_for_tests()` 清空注册表。

**per-user 模型解析**：`_resolve_user_embedding_model(user_id)` 通过
`get_user_llm_configs(user_id)` 拿到该用户 embedding slot 指定的模型；用户在
`user_providers` 里还没配 → fallback 到全局 `embedding_config.model`
（单用户桌面模式继续 work）。

**SQL 都带 user filter**：`_narrative_count_sql()` JOIN agents on
`agents.created_by = :user_id`；`_event_count_sql()` / `_job_count_sql()` 直接
WHERE `user_id = :user_id`；`_entity_count_sql()` JOIN module_instances on
`module_instances.user_id = :user_id`。共享的 `_EVENT_TEXT_FILTER` /
`_JOB_TEXT_FILTER` / `_ENTITY_TEXT_FILTER` WHERE 片段同时用于 count 和 rebuild
查询，确保 "total" 和实际处理数一致。

**per-user 数据清理**：`_cleanup_before_rebuild(model)` 仅删属于当前 user 的
哨兵行（`dimensions=0`）+ 空壳 entity（通过子查询，兼容 SQLite 和 MySQL，
不用 MySQL-only 的 `DELETE alias FROM JOIN` 语法）。

每类实体的 `_*_source_text` 构造函数需要和原始 embedding 生成逻辑保持一致——每个
builder 的 docstring 里有交叉引用。

## Gotcha / 边界情况

**`_should_use_store()` 双 fast-path**：
- 同步 `_resolve_use_embedding_store(user_id)` 看 `llm_config.json` 文件是否存在
  （桌面场景）
- 异步 DB 查询 `user_providers` 是否有该 user 的行（云端场景）
- 两者都 False 才回退 legacy_mode（极罕见：用户没配过任何 provider）

**`use_embedding_store()` 现在无条件 True**（见 `embedding_store_bridge.py`）：
这是 Bug 11 真相之一——原先在云端返回 False，让所有读走 legacy 列，多租户多模型
数据污染。Gate 翻转后 migration service 的 legacy fallback 只在新用户"零配置"
时生效。

**每批（batch_size=20）处理完 asyncio.sleep(0.1)**：防 embedding API rate
limit。API 配额宽裕时可减小加快速度。

**JSON 提取语法**：`_rebuild_narratives` 用 `JSON_UNQUOTE(JSON_EXTRACT(...))`
读 `narrative_info` 的 `name` / `current_summary`。数据库层 `_mysql_to_sqlite_sql`
会把它翻译为 SQLite 的 `json_extract`，所以两种后端都 work。

**rebuild 的进度是进程内**：重启服务后进度归零，但服务本身**支持断点续跑**——每次
`rebuild_all` 都会跳过已有当前 model 向量的实体。UI 进度条从 0 开始只是视觉显示。

## 新人易踩的坑

`EmbeddingMigrationService(db, user_id="")` 直接 raise `ValueError`；构造时必须
传合法 user_id。API 端点 `?user_id=` 为空会 400。

`rebuild_all()` 使用 `BackgroundTasks` 在后台跑（见 `providers.py`）。POST 返回
"started" 后立刻 200，客户端通过轮询 `status` 端点观察进度。不要在前端 await 长
耗时 rebuild。

`MigrationProgress` 的 `completed_count` / `total_count` 是所有 entity type 的
汇总；若单类 entity 在 `rebuild_*` 里抛异常，该类的 `failed` 计数会保留，其他类
照常跑（每类独立 try 在 `_process_rows` 内）。
