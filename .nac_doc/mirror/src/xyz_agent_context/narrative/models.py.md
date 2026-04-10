---
code_file: src/xyz_agent_context/narrative/models.py
last_verified: 2026-04-10
stub: false
---

# models.py — Narrative 模块所有数据模型的唯一来源

## 为什么存在

Narrative、Event、ConversationSession 三个核心数据结构原本分散在多个文件里，导致跨文件循环引用频繁发生。合并到 `models.py` 这一个文件后，任何需要这些类型的地方都只需要 `from .models import ...`，消除了模块内循环导入。

同时，这个文件也是理解整个记忆系统的最佳起点——读完这里的类定义，就能理解系统是如何组织记忆的。

## 上下游关系

**被谁用**：`narrative/` 包内所有文件都从这里导入类型；`agent_runtime/` 的 step 文件通过 `NarrativeService` 间接使用；`repository/narrative_repository.py` 和 `repository/event_repository.py` 用于数据库序列化/反序列化；`services/instance_sync_service.py` 用 `NarrativeActor` 和 `NarrativeActorType`；schema 层的 `ModuleInstance` 被 `Event.module_instances` 引用。

**依赖谁**：只依赖 Python 标准库和 `xyz_agent_context.schema.module_schema.ModuleInstance`。模型层自身是"纯数据"，不引用任何实现逻辑。

## 设计决策

**Narrative 是路由索引，不是内容容器。** `Narrative.routing_embedding` 是用来"找到这条线"的，`event_ids` 是指向事件列表的引用而非事件内容本身。实际的对话内容存在 Event 里，Narrative 只存摘要（`topic_hint`、`dynamic_summary`）。这个设计让 Narrative 对象保持轻量，可以整体加载进内存；Event 按需批量加载。

`NarrativeActorType.PARTICIPANT` 是 2026-01-21 新增的类型，专门支持"目标客户"场景——Job 的目标用户会以 PARTICIPANT 身份加入 Narrative 的 actors，让该用户发消息时也能匹配到这条 Narrative。这条逻辑在 `services/instance_sync_service.py` 的 `_add_participant_to_narrative()` 里实现。

`Narrative.main_chat_instance_id` 字段标注为 Deprecated（2026-01-21），保留仅为数据库兼容性，不要在新代码里读写它。

`NarrativeSelectionResult.evermemos_memories` 是 Phase 2 引入的 EverMemOS 缓存透传字段，格式自由度高（`Dict[str, Any]`）。如果 EverMemOS 未启用，这个字段是空 dict，不影响正常流程。

## Gotcha / 边界情况

`Narrative.is_special` 字段默认是 `"other"`，只有系统预置的 8 个默认 Narrative 会被设为 `"default"`。`ContinuityDetector` 对 default Narrative 有更严格的判断逻辑（一旦用户提到具体话题就切换 Narrative）。如果通过 API 手动创建 Narrative 并设置 `is_special="default"`，会导致这条 Narrative 被连续性检测器异常对待。

`Event.env_context` 是自由 dict，里面存了模型名、执行参数等信息。`EmbeddingMigrationService` 在重建 Event embedding 时会从 `env_context.input` 字段读取输入内容，字段名必须匹配——如果某个触发路径没有在 `env_context` 里写入 `input` key，该 Event 的 embedding 重建会退化到用 final_output 估算。

## 新人易踩的坑

`ConversationSession` 和 `Narrative` 的关联是单向的：Session 持有 `current_narrative_id`，但 Narrative 里没有"谁的 session"字段。查"某用户的当前 Narrative"要通过 SessionService，不要去查 Narrative 表。

`NarrativeSearchResult` 的 `episode_summaries` 和 `episode_contents` 是 EverMemOS 的专有字段，在纯向量检索路径下始终为空列表，不代表 Narrative 没有事件。
