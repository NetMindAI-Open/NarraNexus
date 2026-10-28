---
code_dir: src/narranexus/platform/narrative/_narrative_impl/
last_verified: 2026-08-27
stub: false
---

# _narrative_impl/ — Narrative 服务的私有实现层

## 目录角色

这是 `narrative/` 包的内部引擎室，不对外导出。所有外部调用都经过 `NarrativeService` 门面，`_narrative_impl/` 的类不能被包外代码直接实例化（名称前缀 `_` 就是这个约定）。

十七个文件各司其职：数据库 CRUD、候选召回、数值闸门、LLM 判定、后台摘要更新、连续性
检测、默认 Narrative 管理、Instance 依赖处理、Prompt 构建、合并路由(编排/准备/决策/
prompt/落点/锚点规则)。这种细粒度切分是为了让每个文件足够专注，可以独立修改而不影响
其他部分。

## 关键文件索引

（2026-08-12 核对过：表格与目录逐一对齐。原表列的 `vector_store.py` 已随
2026-06-09 unified-memory 重构删除 —— 向量全部退役，召回是纯 BM25；`routing_gate.py`
是 2026-07-29 新增的，原表缺。）

| 文件 | 职责 |
|------|------|
| `crud.py` | Narrative 的数据库读写，不含业务逻辑；`_index_narrative` 投影进统一记忆索引 |
| `retrieval.py` | BM25 候选召回 + participant 通道 + 组装判官候选；决定"属于哪条线" |
| `routing_gate.py` | 纯函数数值闸门（raw floor + top1/top2 margin），决定要不要跳过判官 |
| `_retrieval_llm.py` | 判官那次 helper LLM 调用（unified match，带 / 不带 PARTICIPANT 两版） |
| `updater.py` | Event 结束后由 helper LLM 全量重写 name / current_summary / topic_keywords |
| `continuity.py` | 判断当前 query 是否仍属于 session 里上一轮那条 Narrative |
| `instance_handler.py` | Instance 完成时处理依赖链，激活 blocked Instance |
| `default_narratives.py` | 系统预置的 8 个默认 Narrative 的定义和初始化逻辑 |
| `prompt_builder.py` | 把 Narrative 序列化成 LLM prompt 片段（稳定半 / turn 半） |
| `prompts.py` | LLM 调用的静态 prompt 模板 |
| `routing_blocks.py` | 路由 prompt 的四个共享渲染块(锚点线 / 上一轮 / 菜单 / participant);三个 tier 共用,continuity 与 judge 的文本字节相同 |
| `merged_router.py` | 合并调用的决策器:build_merged_prompt + decide + allowed_verdicts(prompt 说有的 = 契约收的) |
| `merged_select.py` | 合并路径的**编排**(service 只留薄委托):快门或一次调用 → 按 verdict 落点 → audit |
| `merged_prep.py` | 合并路径的 BM25 准备段:一次 _score_pool + 锚点反事实席位 + audit tier-2 填写 |
| `prompts_merged.py` | 合并指令的片段与 per-turn 组装器 build_merged_instructions(2×2 变体拼接) |
| `landings.py` | 所有 decider 共享的落点执行器 + candidate_labels 唯一定义 + Landing 值对象 + land_no_topic |
| `anchor_rules.py` | 锚点规则唯一定义:is_reusable_anchor / minutes_since / advance_session_anchor |

> `default_narratives.py` 与 `instance_handler.py` **还没有 mirror md**（本次未补：
> 没读过它们，写出来会是凑数的文档）。谁下次动这两个文件，按铁律 #10 顺手补上。

## 2026-08-27 — 合并调用进来之后的内部依赖方向(round 3/5 拆分后的终形)

新增七个文件(routing_blocks / merged_router / merged_select / merged_prep /
prompts_merged / landings / anchor_rules),方向**单向向下**,不许反过来:

```
narrative_service(薄委托)
   └─> merged_select ──> merged_prep ──> retrieval._score_pool
            │                 └────────> routing_gate(pick_menu / shutter_opens)
            ├──> merged_router ──> prompts_merged ──> prompts(_NO_DURABLE_TOPIC_RUBRIC)
            │         └──> routing_blocks <── continuity / _retrieval_llm(字节相同)
            ├──> landings(落点执行器 + Landing)
            └──> anchor_rules(锚点规则三件套)
```

`routing_blocks.py` **不依赖** config / DB / LLM —— 预算常量由调用方传进来,
所以它是纯函数、可以逐字节 golden 测。这条方向性是"字节相同"这个契约能被
测出来的前提;哪天让它自己去读 config,continuity 的 prompt 就会跟着开关变。

`merged_router.py` 只被 `merged_select.select_merged` 调用;
`_narrative_impl/__init__.py` **不导出**这七个新文件(私有实现层的约定),
服务层对 merged_select / landings 按需 function-level import。
retrieval **不得** import merged_router(round 3 I6 拆掉的反向边);
landings 的 `Landing` 是 flag-off 路径也用的返回类型,住这里正是为了
不让 flag-off import 合并模块的 helper-SDK 链。

## 和外部目录的协作

**向上暴露**：通过 `_narrative_impl/__init__.py` 导出 `NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`、`PromptBuilder`、`ContinuityDetector`，供 `NarrativeService` 消费。

**外部依赖**（2026-08-12 核对）：
- `retrieval.py` 依赖 `memory/_memory_impl/retrieval.py` 的 `bm25_explain` / `bm25_snippet`
  （**路由与记忆召回共用同一份 BM25**），`_retrieval_llm.py` 的判官函数，
  `routing_gate.py` 的闸门。EverMemOS 已解耦，只剩几处 `# evermemos_memories removed` 注释
- `updater.py` 依赖 `narrative/config.py` 的 `config.NARRATIVE_LLM_UPDATE_INTERVAL`；
  embedding 相关依赖已随 2026-06-09 重构移除
- `continuity.py` 依赖 `agent_framework/adapters/openai_agents.OpenAIAgentsSDK` 做结构化 LLM 调用，并与 `channel/channel_context_builder_base.py` 的 Matrix 模板格式有隐式耦合（`_extract_core_content()` 函数）
- `instance_handler.py` 被 `services/module_poller.py` 直接从 `narrative` 包导入使用
