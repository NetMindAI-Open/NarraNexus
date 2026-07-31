---
code_file: src/xyz_agent_context/narrative/models.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — TriggerType 与 WorkingSource 1:1 对齐

新增 JOB/A2A/CALLBACK/SKILL_STUDY/LARK/SLACK/TELEGRAM/WECHAT/
NARRAMESSENGER/DISCORD/MANYFOLD 成员：step 0 现在把 working_source
直接映射进 `events.trigger`（原来除 message_bus 外一律记 chat，lark/job
run 全被标成"聊天"）。读侧依赖这个诚实标签：侧栏预览滤 MESSAGE_BUS
（不变）、**聊天页 active_run 自动接管只认 chat/manyfold**（否则 trigger
run 变 running 后会被单聊页面劫持，见 [[auth]]）、dashboard 按来源分组。
repository/crud 的 `TriggerType(row["trigger"])` 回读要求新值必须是合法
成员 —— 这是扩枚举而非直接存字符串的原因。

## 2026-07-29 — `NarrativeSearchResult.raw_score`

新增字段，承载未 squash 的 BM25 原始分。`similarity_score` 保留给展示和 LLM
prompt，但**判据不能用它**：`s/(s+1)` 压缩了候选之间的间距，而间距是这里唯一
可比的信号（IDF 按候选集现算，绝对值无跨 agent 意义）。见 [[routing_gate.py]]。
参与者 narrative 走合成中性分、无 BM25 分，`raw_score` 保持 0.0。

> 2026-06-23：`TriggerType` 新增 `MESSAGE_BUS = "message_bus"`，用于把团队群聊
> (message bus) 的 Event 与 1:1 聊天区分开（侧栏预览据此过滤；见 [[event_service]]
> / [[step_0_initialize]] / [[auth]]）。这是 `Event.trigger` 用的枚举（CHAT/TASK/
> API/TOOL/MESSAGE_BUS/OTHER）——注意另有一个同名 `WorkingSource`/`module_schema`
> 的 `TriggerType`，不是这个。

> 2026-05-29：删除 `EpisodeResult`，并从 `NarrativeSearchResult` 去掉
> `episode_summaries` / `episode_contents` 字段（EverMemOS 整体移除）。

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
