---
code_dir: src/narranexus/platform/narrative/_event_impl/
last_verified: 2026-08-05
stub: false
---

# _event_impl/ — Event 服务的私有实现层

## 目录角色

`_event_impl/` 是 `EventService` 的内部实现，同样遵守"前缀 `_` 不对外导出"的约定。四个核心文件分别处理 Event 的数据库操作、执行后处理、上下文筛选，以及 LLM prompt 构建。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `crud.py` | Event 的数据库 CRUD，支持批量加载（DataLoader 模式解决 N+1）。**没有 `duplicate()`——一次执行只能有一行**，见 [[crud]] |
| `processor.py` | Event 的后处理：`final_output` / `event_log` 写回、上下文筛选（最近 N 条） |
| `prompt_builder.py` | 把 Event 序列化成可注入 LLM 上下文的 prompt 片段 |
| `prompts.py` | LLM 调用的静态 prompt 模板 |

## 和外部目录的协作

**向上暴露**：通过 `_event_impl/__init__.py` 导出 `EventCRUD`、`EventProcessor`、`EventPromptBuilder`，供 `EventService` 消费。

**外部依赖**：
- `crud.py` 可以接受 `EventRepository` 和 `DataLoader[str, Event]` 注入，解决 step_2 里批量加载多条 Narrative 对应 Event 时的 N+1 问题
- `processor.py` 的 `select_for_context()` 的参数默认值来自 `narrative/config.py`（`MAX_RECENT_EVENTS` 等），修改 config 会直接影响上下文长度

> 2026-08-05 更正：本文原来写「`processor.py` 依赖
> `agent_framework/llm/api/embedding.py` 的 `get_embedding()` /
> `cosine_similarity()`，在 `update_event()` 时生成 Event embedding」，并把上下文
> 筛选描述成"最近 N + embedding 相似度 Top-K 混合"。**两者都已不成立**：Event
> embedding 在 2026-06 unified-memory 重构里退役，`events.event_embedding` /
> `embedding_text` 是惰性墓碑列（铁律 #6），`processor.update_event()` 不做任何
> embedding 调用。`select_for_context()` 现在纯按 recency（最近 N 条、按
> `max_total` 截断、保持原顺序）；跨 Narrative 的语义召回搬去了统一
> MemoryEngine 的 BM25。
