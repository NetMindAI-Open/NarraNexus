---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/_entity_updater.py
last_verified: 2026-08-25
---

## 2026-08-24 — 八处静默吞异常改为「可分辨 + 可上报」

本文件每个函数原来都是 catch → log → 返回一个空值。**调用方分不清「LLM 跑完
了没找到东西」和「LLM 死了」**：`summarize_new_entity_info` 返回 `""` 两种
含义都有，调用方两种情况都跳过写入。于是一把过期的 helper key 会让长记忆
无声退化——2026-07 那次持续了两周。

这不是抽象风险。8/14 乒乓事故里，agent 对那个已经交换了 6.6 万条消息的对端，
profile 仍然停在「第一次见面」，所以它**自己的上下文里没有任何东西**能告诉
它正在循环。记忆写入的静默失败和那个死循环是同一根链条。

**两条修法，八处全覆盖：**

1. **失败与空结果可分辨**。`summarize_new_entity_info` 和 `infer_persona`
   改成 `Optional[str]`，`None` = 失败（铁律 #2，直接改签名不留 shim）。
   `infer_persona` 原来失败时返回**当前** persona，调用方原样写回——一次
   no-op 写入和一次成功刷新长得一模一样。
2. **失败会上报**。LLM 调用点走
   [[background_llm_alerts.py]] 的 `alert_background_llm_failure`；
   DB 写入点走 `ServiceAuditor` 审计行，**不发收件箱通知**——一次失败的
   UPDATE 是我们的 bug 或基础设施问题，不是用户换个 key 能修的，为它打扰
   用户是 alarm fatigue。

**review 后补的几件（PR#360）：**

- **owner 解析走 `AgentRepository.resolve_owner`**，不再手搓
  `get_one("agents", ...)`。仓库里那个 resolver 的 docstring 明写「the ONE
  answer」，`backend/routes/channels/wechat.py` 还留了一条明确禁令——第一版
  在这里开了第四份副本，正是 PR #258 收敛掉的那种漂移。它区分 `""`（agent
  不存在）与 `None`（查询本身失败），**不要**用 `or ""` 把两者塌回去。
- **`infer_persona` 的三档语义**：`None` = 调用失败 / `""` = 跑通了但没有
  要改的 / 有值 = 新鲜。第一版只修了失败那档，「跑通但输出为空」仍然回吐
  当前 persona——调用方原样写回，日志打「persona updated」，还是假成功，
  只修了一半。
- **两个 audit service 名**：LLM 失败走 `background_llm`（可能打扰 owner），
  DB 写入失败走 `social_network_memory`（只落审计行）。「记忆为什么停更」
  的排查要覆盖两个名字。
- **调用方那一侧也报**：`social_network_module` 里主实体创建失败
  （`create_primary_entity`，最重的一处——建不出来整个 turn 的记忆更新全部
  跳过）和逐实体处理失败（`process_mentioned_entity`，覆盖第三方实体创建、
  tags/aliases 合并、以及 Stage-1 的**读**失败）现在都留审计行。铁律 #8：
  记忆写入并不只在本文件里。
  **前提**：内层八个 handler 自己报过且都不 re-raise，所以外层 except 只会
  看到「不是它们报过的」失败，今天不存在双报——**日后谁给内层加 re-raise，
  必须同时处理这个前提**。
- **`service_audit` 没有保留期**，而本次改动把最热路径的写入量放大到每回合
  最多 5 行（summary / extraction / dedup / compression / persona）。这是
  **有意接受**的：`background_llm` 早就是同样的无界 error-only 命名空间，
  且铁律 #15 说得清楚——用户挑了不稳的模型是他的权利，平台自己扛下这个写入
  量。真要加保留期是独立改动（`ServiceAuditRepository` 补
  `cleanup_older_than_days`，沿用 `AUDIT_RETENTION_DAYS = 30`）。

**几个不是「顺手」的细节：**

- `decide_merge_or_create` 失败时 fallback 到 CREATE_NEW，形状是对的（宁可
  多一个重复节点也别丢实体），但**不是没有代价**：每次失败都在分叉社交图。
- `update_interaction_stats` 看着像计数器，其实 `should_update_persona` 靠
  它每 N 次触发——一处吞掉的异常静默关掉了第二个功能。
- `compress_description` 失败时返回截断值，**看起来是成功的**，但静默丢掉了
  切口之后的全部内容，所以照样上报。
- `owner_user_id` 在这条路上拿不到（hook 是脱钩后台任务，比解析 owner 的地方
  低好几层），所以 `_report_llm_failure` 要反查——走
  `AgentRepository.resolve_owner`（见上方 2026-08-25 那条，**不要**手搓
  `get_one("agents", ...)`）。查不到也没关系，告警的审计层照样落行。

## 2026-08-17 — 记一个已知缺口：`should_update_persona` 的 change-signal 只认英文

本次**没改代码**，只是把一个原本只存在于一份已删除文件里的期望搬到有人会读的
地方。

`should_update_persona()` 判断"对话里出现显著变化信号"用的是写死的英文字面量
表（`"i changed my mind"` / `"budget changed"` / ...）。被删掉的
`src/.../social_network_module/test_persona.py` 里曾有一条用例断言中文也该触发
（`"我改变主意了……"`）——那条断言**从未通过过**（它调的方法早已重构到本文件，
而脚本从不被 `make test` 执行），所以这不是回归，是一个从未实现的期望；但它是
该期望唯一的书面记录，而产品对中文用户上线。

两个问题别混着修：(1) 非英文说法不触发重新推断，用户只会在 agent 继续按旧画像
说话时察觉，没有任何报错；(2) `"budget changed"` / `"decision process changed"`
是销售场景词汇，写死在通用模块里已经违反铁律 #4，**补一份中文表会让这个违规加
倍，不是修好它**。方向应是让本函数不再自带任何场景词表（交给 Awareness 声明，
或并入既有 LLM 判断），而不是加语言。

优先级不高的原因：每 10 轮的周期性刷新是保底路径，所以漏掉信号只是**延迟**重新
推断，不是永不推断。

守卫：`tests/social_network_module/test_persona_refresh.py`（只覆盖当前英文行为
+ turn-0 与大小写边界）。详细讨论在本地 `reference/self_notebook/todo/`
（该目录 gitignored，故此处留正本）。

## 2026-07-23 — meaningfulness guard (junk-entity filter)

`ExtractedEntity` gained `confidence` (default 1.0 so old outputs pass);
`is_meaningful_entity()` is the deterministic backstop applied inside
`extract_mentioned_entities` before create/merge: rejects generic
role/category names (en+zh set), bare system IDs / pure digits / uuid /
long-hex blobs, names > 80 chars, and confidence < 0.5. Rationale: the
prompt already forbids these but weak helper models leak them and every
leaked row is a permanent junk node in the graph (bug "entity 图无意义
条目"). Dropped entities are logged at INFO. Prompt gained a Confidence
section ([[prompts.py]]). Tests:
`tests/social_network_module/test_entity_filter.py`.
## 2026-06-10 — helper obtained via get_helper_sdk()

All five llm_function call sites switched to the protocol-agnostic
`get_helper_sdk()` factory (single-Claude-key users get the Messages-API
helper). Call shapes unchanged.


## 2026-05-27 — semantic-search 链路全删（Owner spec, scope B）

`update_entity_embedding`、`DEDUP_SIMILARITY_THRESHOLD`、`DEDUP_TOP_K` 都
被删了。Mentioned-entity 的 dedup 现在只剩 **Stage 1 (name/alias exact
match) → LLM 拍板 MERGE/CREATE**，不再做向量相似度检索 ("Bob" vs
"Robert" 异名同人会产生两条记录，后续靠人工/LLM 合并)。

同时 `get_embedding` import 也被删，文件不再依赖
agent_framework 的 embedding 工具（该子系统现已整体移除）。

为什么这么彻底——参见 [[social_network_module.py]] 的 2026-05-27 条目和
[[social_network_repository.py]] 的同期改动。

# _entity_updater.py — LLM 驱动的实体更新管道

## 为什么存在

从 `social_network_module.py` 分离出来（2026-03-06），把所有"需要调用 LLM 来更新实体信息"的逻辑集中维护。`social_network_module.py` 里的 `after_turn` 只做编排——它调用这里的函数来完成摘要生成、描述追加、Persona 推断等具体操作。

核心操作（2026-05-27 修订）：
1. `summarize_new_entity_info`：从本次对话提炼新信息（LLM）
2. `append_to_entity_description`：追加到 `entity_description`，超长时自动压缩（LLM）
3. `update_interaction_stats`：递增 `interaction_count`，更新 `last_interaction_time`
4. `should_update_persona` / `infer_persona` / `update_entity_persona`：按条件触发的 Persona 推断（LLM）
5. `extract_mentioned_entities`：从对话中批量提取提及的其他实体（LLM）
6. `decide_merge_or_create`：dedup 阶段的 LLM 仲裁（MERGE / CREATE_NEW）

## 上下游关系

- **被谁用**：`SocialNetworkModule.after_turn()` 按顺序调用这里的函数
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

- 这里所有 LLM 调用都是 `await` 的异步调用，但 `after_turn` 本身是异步的且是 fire-and-forget 风格（见 `MemoryModule` 的类似模式）。如果 LLM API 超时，这里的错误会被上层的 try/except 静默捕获，实体更新失败但不影响主流程。
