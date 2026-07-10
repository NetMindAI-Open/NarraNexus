---
code_file: src/xyz_agent_context/narrative/_narrative_impl/updater.py
last_verified: 2026-07-07
stub: false
---
# updater.py — Narrative 更新 + LLM 动态摘要生成

## 为什么存在

`NarrativeUpdater` 负责把一个 Event 写进 Narrative，并随对话演进动态刷新 Narrative
元数据（name / current_summary / topic_keywords / actors / dynamic_summary）。

两条路径：

- **同步基本更新**（`update_with_event`）：追加 event_id、临时写一条 dynamic_summary
  （取 `final_output` 前 200 字），存库。Default Narrative 只追加 event_id 不做别的。
- **异步 LLM 更新**（`_async_llm_update`，仅 main_narrative 触发）：每攒够
  `NARRATIVE_LLM_UPDATE_INTERVAL` 个 event 就 fire 一个 `asyncio.create_task`，调 LLM
  把动态摘要压成结构化 fact sheet。非阻塞主流程。

并发安全是这个文件的核心设计点：`update_with_event` 和 `_apply_llm_update` 都会先
`load_by_id` **重新从库里拉最新 Narrative** 再改，避免拿着流程开头的 stale 对象覆盖掉
并发写入（典型是别的进程刚加的 PARTICIPANT actor）。`_apply_llm_update` 还刻意**只改
LLM 生成的字段（name / summary / keywords），不碰 actors**，保住库里最新的参与者。

辅助 Narrative 目前只做基本更新、跳过 LLM 更新（视角不同，需专门的 prompt，TODO）。
Embedding 那套机器在 2026-06-04 unified-memory 重构时已移除——路由改成 name/summary/
keywords 上的 BM25，相关 DB 列（routing_embedding 等）按铁律 #6 留作惰性墓碑，无人读写。

上游：Event 执行收尾后被调用。EverMemOS 写入已迁到 `MemoryModule.hook_after_event_execution()`。

## 2026-06-17 — LLM 调用切到 protocol-agnostic 的 get_helper_sdk()

PR #25 把 `_call_llm_for_update` 里的 `OpenAIAgentsSDK()` 直接实例化改成
`get_helper_sdk()`。与全仓 helper LLM 收敛一致（铁律 #9）：摘要生成用的 helper 不绑死
OpenAI Agents SDK，底层可换而本文件不动。`model` / `reasoning_effort` 仍取自
`narrative_config.NARRATIVE_LLM_UPDATE_*`，调用契约与更新逻辑均不变。

## 2026-07-07 — 后台 LLM 更新必须用 owner 的 Helper LLM，不再落到平台 key

`_async_llm_update` 是 `asyncio.create_task` 出去的**脱离任务**——它不继承
`AgentRuntime.run`（一个 async generator）在自身 ContextVar 上设的 per-turn helper 配置。
在此修复前它裸调 `get_helper_sdk()`，于是 `_ConfigProxy` 一路回退到全局 `_holder`
= 平台的 `settings.openai_api_key`。2026-07 事故：平台 OpenAI key 过期后，走 bus/jobs
的后台 narrative 更新全部 401，且被 `_call_llm_for_update` 的宽 `except → return None`
静默吞掉，长记忆退化约两周无人察觉。

两处改动：
1. `_async_llm_update` 开头调 `inject_owner_helper_credentials(narrative.agent_id, db)`
   （provider_resolver 里的共享原语，走完整 ProviderResolver：用户自配则用自配，免费额度
   用户走系统 provider）。解析失败（配额耗尽/无 provider）**不再落到平台 key**——跳过更新
   并 `alert_background_llm_failure`（service_audit 审计 + owner inbox 通知）。
2. `_call_llm_for_update` 不再吞异常 `return None`，异常上抛；`_async_llm_update` 对
   凭据类异常（`is_credential_error`）发告警，瞬时异常仍仅记日志。
