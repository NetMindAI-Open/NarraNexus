---
code_file: src/xyz_agent_context/narrative/config.py
last_verified: 2026-05-29
stub: false
---

> 2026-05-29：删除全部 `EVERMEMOS_*` 常量（EverMemOS 整体移除）。Narrative
> 检索现在无条件走本地 VectorStore，没有外部检索后端开关。

# config.py — Narrative 系统所有可调参数的中央控制台

## 为什么存在

Narrative 检索、连续性判断、embedding 更新是计算密集型操作，各阶段的阈值直接影响系统的记忆质量和 API 成本。把所有参数集中在一个单例对象里，有几个好处：实验调参时只需改一处；文档注释就在代码旁边，解释每个参数的含义和推荐范围；生产与开发环境可以通过替换此对象的字段来切换行为，不需要散落在各处的 if/else。

## 上下游关系

**被谁用**：`_narrative_impl/retrieval.py` 读 `NARRATIVE_MATCH_HIGH_THRESHOLD`、`NARRATIVE_SEARCH_TOP_K`、`EVERMEMOS_*` 系列参数；`_narrative_impl/updater.py` 读 `NARRATIVE_LLM_UPDATE_MODEL`、`EMBEDDING_UPDATE_INTERVAL`；`_narrative_impl/continuity.py` 读 `CONTINUITY_LLM_MODEL`；`_event_impl/processor.py` 读 `MAX_RECENT_EVENTS`、`MAX_RELEVANT_EVENTS`；`session_service.py` 读 `SESSION_TIMEOUT`。

**依赖谁**：无外部依赖，纯 Python 类。文件末尾导出单例 `config = NarrativeConfig()`，调用方通过 `from .config import config` 获取。

## 设计决策

所有参数都有行内注释解释推荐值、调参建议和适用场景，这是刻意的——这个文件就是系统的"调参手册"，不依赖外部文档。

`NARRATIVE_LLM_UPDATE_INTERVAL` 这个参数**不在这里**，而是在 `xyz_agent_context/config.py`（全局 config）里——因为它控制的是 LLM API 调用频率，是运营成本相关的全局参数，不是 Narrative 内部的行为参数。这个分工容易让新人找错地方。

`EVERMEMOS_ENABLED = False` 现在是默认值——云端部署目前没有运行 EverMemOS 服务，开着会让 backend 在每次 hook 写入时打 ConnectError。打开前先确保 EverMemOS 服务已经跑起来；retrieval.py 在禁用时直接走纯向量检索路径，不会触碰 HTTP 客户端。配套的 belt-and-suspenders 在 `utils/evermemos/client.py:get_evermemos_client` 里——禁用时返回 no-op stub，覆盖那些没显式 gate 的调用方。

`ENABLE_HIERARCHICAL_STRUCTURE = False` 和 `ENABLE_AUTO_SPLIT = False` 是 Phase 2 预留的功能开关，目前代码里没有对应实现，改成 True 没有效果。

## Gotcha / 边界情况

`VECTOR_SEARCH_MIN_SCORE = 0.0` 意味着向量搜索没有最低分过滤，所有 Narrative 都会进入候选集再由 LLM judge 裁决。这是有意设计的，用宽松召回 + 精准 LLM 判断替代严格阈值过滤，但候选集如果很大（几百条 Narrative），LLM judge 的 prompt 会很长。如果发现 LLM judge 超出 token 限制，可以调高这个值做初步过滤。

`EMBEDDING_MODEL = "text-embedding-3-small"` 修改后需要重新生成所有历史 embedding，因为新旧模型的向量空间不兼容，直接混用会导致语义检索结果完全错乱。有专门的 `EmbeddingMigrationService` 处理这种迁移，但需要手动触发。

## 新人易踩的坑

`MAX_NARRATIVES_IN_CONTEXT = 3` 控制的是 select() 返回的 Narrative **数量上限**，不是每条 Narrative 注入多少事件。事件数量上限由 `MAX_EVENTS_IN_CONTEXT = 6` 控制，这两个数字容易混淆。
