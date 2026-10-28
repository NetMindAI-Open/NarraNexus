---
doc_type: reference
last_verified: 2026-04-10
scope:
  - src/narranexus/platform/narrative/
  - src/narranexus/platform/narrative/_narrative_impl/
  - src/narranexus/platform/narrative/_event_impl/
related_playbooks:
  - ../playbooks/debug_runtime.md
---

# Narrative 系统参考文档

## 1. Narrative 是什么

Narrative 是一个**有状态的对话容器**，代表用户在某个 Agent 下的一个话题或故事线。每个 Narrative 持有以下核心数据：

- **身份标识**：`id`、`agent_id`、类型（`CHAT`、`TASK`、`OTHER`）
- **内容**：`narrative_info`（名称、描述、参与者）、`event_ids`（按时间排序的 Event 引用列表）
- **Instance 绑定**：`active_instances`、`instance_history_ids`
- **路由索引**：`routing_embedding`（1536 维向量）、`topic_keywords`、`topic_hint`

特殊标记：`is_special="default"` 用于标识内置的兜底 Narrative（如 "greeting"、"general_inquiry"），这些 Narrative 拥有更严格的话题边界。

需要注意：Narrative **不直接存储记忆**。它通过 `event_ids` 引用独立存储的 Event 记录。

## 2. Narrative 选择流程

选择分为两阶段：

### Phase 1 — 话题连续性检测（Continuity Detection）

当存在会话上下文（近期有上一轮查询）时触发：

- 调用 `ContinuityDetector.detect()` 通过 LLM 判断："当前查询是否属于当前 Narrative？"
- **输入**：`previous_query`、`previous_response`、`current_query`、`time_elapsed`、Narrative 元数据
- **输出**：`is_continuous`（布尔）、`confidence`（0-1）、`reason`（原因说明）
- **关键洞察**：对话连续性 != 同一 Narrative。用户可能连续对话但切换了话题。
- Default Narrative 的边界非常严格——任何涉及具体对象或任务的提及都会触发切换。

### Phase 2 — Top-K 向量检索（Retrieval）

当 Phase 1 判定"不连续"或不存在会话上下文时触发：

- `NarrativeRetrieval.retrieve_top_k()` 执行语义搜索（1536 维 embedding）
- 同时使用 EverMemOS 的 episode summary 作为兜底补充
- 返回按相关性排序的 Narrative 列表
- 若无匹配结果 -> 自动创建新 Narrative

最终结果封装为 `NarrativeSelectionResult`，包含 narratives 列表（主 Narrative 在位置 0）、`query_embedding`、选择方法标识。

## 3. Instance 与 Narrative 的绑定关系

### 核心关系（存储在 `instance_narrative_links` 表）

- 一个 Narrative -> 多个 Instance（通过 `active_instances`）
- 一个 Instance -> 多个 Narrative（通过 `link_type="shared"`）

### LinkType 枚举

| 值 | 含义 |
|---|------|
| `ACTIVE` | 当前关联中 |
| `HISTORY` | Instance 已完成或已移除 |
| `SHARED` | 从另一个 Narrative 共享而来 |

### 绑定生命周期

1. **创建** — Instance 创建后，与 Narrative 建立 `link_type=ACTIVE` 链接
2. **完成** — Instance 完成后，通过 `InstanceNarrativeLinkRepository.unlink(to_history=True)` 标记为 `HISTORY`
3. **依赖激活** — `InstanceHandler.handle_completion()` 检查依赖关系：若所有前置依赖均为 `HISTORY`，则将 `BLOCKED` 状态的 Instance 转为 `ACTIVE`

### 加载逻辑（流水线 Step 2）

- 获取公共 Instance（Agent 级别，对所有用户可见）
- 从 links 表获取 Narrative 关联的 Instance（仅加载 `ACTIVE` / `IN_PROGRESS` 状态）
- ChatModule：仅加载当前用户的 Instance
- 按 `instance_id` 去重

## 4. ContextData 流转

ContextData 是连接 Narrative 选择 -> Module Hook -> LLM 调用的**核心数据总线**。

逐步流转过程：

1. **Narrative 选择** -> ContextData 获得：`narrative_id`、`agent_id`、`user_id`、`input_content`
2. **Step 2: 加载模块** -> 加载活跃 Instance，决定执行路径（`AGENT_LOOP` vs `DIRECT_TRIGGER`）
3. **Step 2.5: 同步 Instance** -> 在数据库中建立/移除链接关系
4. **`gather`** -> 各模块填充 ContextData：
   - `ChatModule`：`chat_history`
   - `BasicInfoModule`：`agent_name`、`created_by`
   - `AwarenessModule`：`awareness` 画像
   - `GeminiRAGModule`：`rag_keywords`、搜索结果
   - `SocialNetworkModule`：实体摘要
5. **Prompt 组装** -> 系统提示词由以下部分构建：Module 指令（按优先级排序）+ Narrative 上下文 + Event 历史 + `extra_data` 段
6. **LLM 调用** -> messages（system + history + context）+ MCP URLs
7. **执行后处理** -> 创建新 Event，追加到 `Narrative.event_ids`

## 5. Event 与跨轮记忆

### Event 结构

每次用户与 Agent 的交互创建一个 Event，记录：

- `module_instances`：使用的模块实例
- `event_log`：所有推理步骤
- `final_output`：最终输出
- `event_embedding`：用于向量搜索的 embedding
- 关联的 `narrative_id`

### 跨轮记忆恢复流程

1. 用户发送新查询 -> 选择 Narrative -> 获取 `event_ids`
2. `EventService.select_events_for_context()` 选择 Top-K Event：
   - 最近 N 条 Event（保持对话连续性）
   - 按语义相关性排序的 Top-K（embedding 相似度）
3. Event + Instance 状态加载到 ContextData
4. LLM 获得当前轮次的完整上下文
5. 新 Event 持久化 -> 追加到 Narrative 供下一轮使用

### Instance 状态持久化

每个 `ModuleInstance` 拥有 `state: Dict[str, Any]`（数据库中的 JSON 列）。示例：
- `ChatModule`：对话历史
- `JobModule`：`job_config`、`next_run_time`、`progress`

## 6. Module 与 Narrative 的协作模式

### 能力类模块（始终在线）

Agent 级别的 Instance（Awareness、SocialNetwork、BasicInfo、RAG）在**每个 Narrative** 中都会被加载。Narrative 级别的 Instance（如 ChatModule）按用户和 Narrative 隔离。这些模块通过 `gather` 丰富 ContextData，但不直接管理 Narrative。

### 任务类模块（JobModule）

- 由 LLM 决策创建（`InstanceDecision`）
- 可通过 `forced_narrative_id` 绑定到特定 Narrative
- 依赖关系构成 DAG（Job A -> Job B -> Job C）
- 完成时触发 `InstanceHandler.handle_completion()` -> 激活下游依赖

### 新 IM 集成模式（如飞书、Telegram）

- 应作为**能力类模块**实现（配置后始终可用）
- Instance 范围：Agent 级别（类似 Awareness）或 Narrative 级别（类似 Chat）
- 触发方式：采用共享 poller/subscriber + 路由分发（architecture.md 中的 Pattern B），而非每个 Agent 独立进程
- 通道：注册 `ChannelSender` + `ChannelTag` 用于响应路由
- 记忆：复用现有 Event 系统——每条飞书消息 -> Event -> 持久化到 Narrative

## 7. 已知陷阱

- `Narrative.main_chat_instance_id` 已**废弃**（2026-01-21 重构）。应使用 `instance_narrative_links` 表。
- `ModuleInstance.narrative_id` 数据库字段同样已废弃。规范数据源是 links 表。
- Default Narrative（`is_special="default"`）的话题边界非常严格。如果你的模块创建的 Instance 需要跨话题持续存在，请使用 Agent 级别（`is_public=True`）Instance，而非 Narrative 级别。
- 连续性检测会在 LLM 处理前剥离通道模板包装（如 Matrix 头部等）。
- Event 选择是混合策略（最近 + 相关），并非纯时间顺序。不要假设 Event 总是按序排列。
