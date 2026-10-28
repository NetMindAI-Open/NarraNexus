---
code_file: src/narranexus/platform/narrative/_narrative_impl/crud.py
last_verified: 2026-09-04
stub: false
---
## 2026-08-07 — `_index_narrative` 不再自己拼文本

改调 `Narrative.searchable_text()`。此前它自己列了同样四个字段但用 `"\n".join`，
而路由侧用 `" ".join`——当前分词器下等价，属于典型的「两份拷贝迟早漂移」。现在是
同一个函数，不是碰巧列了同样字段的第二份实现。详见 [[models.py]]。


# crud.py — Narrative CRUD (+ search-index projection)

## 2026-08-20 — `create` 是三个写入口的唯一漏斗,description 在这里封顶

`description` 现在过 `truncate_text(..., DESCRIPTION_MAX_LENGTH)`。

**为什么封在这里而不是调用点**:写 description 的有三个门 ——
路由的 `create_from_query`、LLM 的 `create_narrative` 信号
(`step_4_persist_results`,文本直接来自 tool 参数)、以及 HTTP 路由。
只修路由那一个会留两个开着,而 LLM 那个门的内容完全不受我们控制。
prod 实测 max description = **198,398 字符**,而 description 在 BM25 索引里、
updater 永不重写 —— 这里一次无界写入就是一块永久化石。
(路由调用点也另外截一次:调用点表达意图,漏斗兜住其余两个写方。)

**`current_summary` 写的是占位符,不是记录**:`PROVISIONAL_SUMMARY_PREFIXES[0]`。
这个字面量必须从 models 导入 —— 出生证退休规则读同一份前缀,两边漂开的唯一
症状是新线悄悄变得检索不到。改这行前先读 models 的 mirror。


## Why it exists

`NarrativeCRUD` is the private create / load / save / query implementation behind `NarrativeService` for the `narratives` operational table. `create()` provisions a new narrative (default user+agent actors and a ChatModule chat instance via `InstanceFactory`, ensuring agent-level instances exist). `save()` / `upsert()` persist it; `load_by_id` / `load_by_agent_user` read it back.

## 2026-06-08 — narrative projected into the unified search index

`save()` and `upsert()` now call `_index_narrative()` after persisting. It projects the narrative's searchable surface (name + current_summary + description + topic_keywords) into `memory_narrative` via `MemoryEngine.index('narrative', id, …)`, with a `source_ref` pointer back to the narrative. This is the SINGLE write point — `create()` flows through `save()` too — so narratives stay findable via `remember` and never go stale. The projected fields are deliberately the SAME ones narrative ROUTING uses (`retrieval.py`), and both share `bm25_rank`, so turn-routing and `remember` rank narratives on identical text. Best-effort: an index failure never breaks narrative persistence.

## Upstream / Downstream

`NarrativeService` → `NarrativeCRUD` → `NarrativeRepository` (operational `narratives` table) + `MemoryEngine` (search projection). `InstanceFactory` for chat-instance provisioning on `create()`.

## Gotchas

- The narrative operational row is the source of truth; `memory_narrative` is a read-only search projection. Deleting a narrative row does NOT currently cascade-delete its index row (known gap — orphan index pointer; tracked in TODO-unified-memory-overhaul.md).

## 2026-09-04 · chat instance class from the record (batch 5b.2)

The in-memory ModuleInstance mirrors `chat_instance_record.module_class` instead of naming ChatModule.
