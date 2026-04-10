---
code_dir: src/xyz_agent_context/narrative/_event_impl/
last_verified: 2026-04-10
stub: false
---

# _event_impl/ — Event 服务的私有实现层

## 目录角色

`_event_impl/` 是 `EventService` 的内部实现，同样遵守"前缀 `_` 不对外导出"的约定。四个核心文件分别处理 Event 的数据库操作、执行后处理（embedding 生成）、上下文筛选（混合策略），以及 LLM prompt 构建。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `crud.py` | Event 的数据库 CRUD，支持批量加载（DataLoader 模式解决 N+1）|
| `processor.py` | Event 的后处理：embedding 生成、上下文筛选（最近 N + 相关 Top-K 混合）|
| `prompt_builder.py` | 把 Event 序列化成可注入 LLM 上下文的 prompt 片段 |
| `prompts.py` | LLM 调用的静态 prompt 模板 |

## 和外部目录的协作

**向上暴露**：通过 `_event_impl/__init__.py` 导出 `EventCRUD`、`EventProcessor`、`EventPromptBuilder`，供 `EventService` 消费。

**外部依赖**：
- `processor.py` 依赖 `agent_framework/llm_api/embedding.py` 的 `get_embedding()` 和 `cosine_similarity()`，在 `update_event()` 时生成并存储 Event 的 embedding 向量
- `crud.py` 可以接受 `EventRepository` 和 `DataLoader[str, Event]` 注入，解决 step_2 里批量加载多条 Narrative 对应 Event 时的 N+1 问题
- `processor.py` 的 `select_for_context()` 的参数默认值来自 `narrative/config.py`（`MAX_RECENT_EVENTS`、`MAX_RELEVANT_EVENTS` 等），修改 config 会直接影响上下文长度

上下文筛选策略的核心是：先取最近 N 条保证连贯性，再按 embedding 相似度取 Top-K 保证相关性，最后合并去重按时间排序。这个"最近+相关"混合策略是为了平衡"我们刚才说到哪了"和"这个问题最相关的历史"两种需求。
