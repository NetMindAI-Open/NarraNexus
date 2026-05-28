---
code_file: src/xyz_agent_context/module/social_network_module/_entity_updater.py
last_verified: 2026-05-27
---

## 2026-05-27 — semantic-search 链路全删（Owner spec, scope B）

`update_entity_embedding`、`DEDUP_SIMILARITY_THRESHOLD`、`DEDUP_TOP_K` 都
被删了。Mentioned-entity 的 dedup 现在只剩 **Stage 1 (name/alias exact
match) → LLM 拍板 MERGE/CREATE**，不再做向量相似度检索 ("Bob" vs
"Robert" 异名同人会产生两条记录，后续靠人工/LLM 合并)。

同时 `get_embedding` import 也被删，文件不再依赖
`agent_framework.llm_api.embedding`。

为什么这么彻底——参见 [[social_network_module.py]] 的 2026-05-27 条目和
[[social_network_repository.py]] 的同期改动。

# _entity_updater.py — LLM 驱动的实体更新管道

## 为什么存在

从 `social_network_module.py` 分离出来（2026-03-06），把所有"需要调用 LLM 来更新实体信息"的逻辑集中维护。`social_network_module.py` 里的 `hook_after_event_execution` 只做编排——它调用这里的函数来完成摘要生成、描述追加、Persona 推断等具体操作。

核心操作（2026-05-27 修订）：
1. `summarize_new_entity_info`：从本次对话提炼新信息（LLM）
2. `append_to_entity_description`：追加到 `entity_description`，超长时自动压缩（LLM）
3. `update_interaction_stats`：递增 `interaction_count`，更新 `last_interaction_time`
4. `should_update_persona` / `infer_persona` / `update_entity_persona`：按条件触发的 Persona 推断（LLM）
5. `extract_mentioned_entities`：从对话中批量提取提及的其他实体（LLM）
6. `decide_merge_or_create`：dedup 阶段的 LLM 仲裁（MERGE / CREATE_NEW）

## 上下游关系

- **被谁用**：`SocialNetworkModule.hook_after_event_execution()` 按顺序调用这里的函数
- **依赖谁**：`OpenAIAgentsSDK.llm_function(output_type=...)`（所有 LLM 调用）；`SocialNetworkRepository`（DB 读写）；`prompts.py` 里的 LLM 提示词（`ENTITY_SUMMARY_INSTRUCTIONS` 等）

## 设计决策

**累积式描述，不覆盖**：`append_to_entity_description()` 把新摘要追加到现有 `entity_description` 末尾（带时间戳），而不是替换。这保留了历史信息。当描述超过某个长度阈值时，触发 `compress_description()`（LLM 压缩），把历史内容浓缩但保留关键事实，防止字段无限增长。

**批量实体提取**：`extract_mentioned_entities()` 分析对话里提及的第三方实体（"ta 提到了他的老板张三"），自动创建或更新这些周边人物的档案。这是被动的社交图谱扩张——不需要用户主动介绍，对话内容就能让 Agent 了解用户社交圈。

**`ExtractedEntity` Pydantic 输出 schema**：LLM 批量提取时输出 `BatchExtractionOutput`（含 `entities: List[ExtractedEntity]`），每个实体有 `name`、`entity_type`、`summary`、`tags`。使用结构化输出而不是自由文本解析，避免手工 parsing 的脆弱性。

**标签 dedup 上限 10**：新提取的 tags 在 merge 时做大小写不敏感的去重，并把每个实体的总标签数上限设为 10。这是为了防止 tag 膨胀导致语义漂移。

## Gotcha / 边界情况

- **`should_update_persona()` 的触发条件**：基于 `entity.interaction_count` 和 `final_output` 的长度，每隔 N 次或输出足够长时才触发。条件逻辑在这个函数里，不在 `social_network_module.py` 里——修改 Persona 更新频率就改这里。
- **批量提取的模糊匹配**：如果已有实体的精确 ID 匹配失败，会用实体名做 `keyword_search` 取前 3 条并选 `interaction_count` 最高的作为匹配项。这可能把不同的人错误归并（如同名用户）。

## 新人易踩的坑

- 这里所有 LLM 调用都是 `await` 的异步调用，但 `hook_after_event_execution` 本身是异步的且是 fire-and-forget 风格（见 `MemoryModule` 的类似模式）。如果 LLM API 超时，这里的错误会被上层的 try/except 静默捕获，实体更新失败但不影响主流程。
