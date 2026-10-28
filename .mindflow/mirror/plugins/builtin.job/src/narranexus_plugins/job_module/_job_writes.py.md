---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/_job_writes.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — `_resolve_job_owner`：job 的归属有 ground truth

创始缺陷：bus 轮次的 `user_id` 是**发送方**（[[message_bus_trigger]] 传
`sender_agent_id`），于是团队房里要的 job 被登记在 `usr_<uid>` 或对端 agent_id
名下——一个不存在的 owner。owner 的 Jobs 列表永远空着，执行时加载的是没有主人的
上下文，而 agent 报告成功。

**不在 [[_mcp_identity]] 的 `resolve_caller_user_id` 里修**：那条路只覆盖占位
符，并且注释写明多用户流里「看似真实但不匹配」的值可能是合法的。那是关于通用身
份策略的判断，保持不动。「这个 job 是谁的」是个**更窄且库里有答案**的问题
（`agents.created_by`），所以在写入点回答——一个点同时覆盖本地 MCP 进程和云端
seam 路由。

**fail open**：`resolve_owner` 用 ""(不存在)/None(查询失败) 区分两种情况，两者都
不构成「调用方错了」的证据，而把字段清空会直接丢掉这个 job。差异只**记日志不静
默纠正**——那行日志正是通用路径注释要的那份测量。

`related_entity_id` 不动：它回答「关于谁」，是另一个问题，也是受支持的形状。

# _job_writes.py — 共享的 job 写实现

## 为什么存在（PR-8b）

`update_job_from_args` 是 job_update 那 ~90 行 build-updates 逻辑（effective_type
顺序、trigger_config+compute_next_run、next_run_time、status 校验 →
JobInstanceService.update_job）的**唯一**实现。它原本三份漂移：MCP 工具、
`backend/routes/jobs.py` PUT /{job_id}、以及本次新增的 agent-scoped seam 路由。
`effective_type` 在 compute_next_run **之前**解析是承重的 zombie-bug 修复（pre-open
review #4）：one_off→scheduled 换 cron 时必须用**新** type 算 next_run，否则算成
one_off→None、job 静默不再运行。合成一份是唯一不在某条 path 上重开这个 bug 的办法。

## 契约

方言安全（JobRepository/JobInstanceService，无裸 SQL）。返回完整 dict、从不抛。
agent-scoped：别的 agent 的 job 读作 "not found"（无存在性 oracle——旧 MCP 工具会漏
"does not belong to agent X"，本实现取路由的更安全措辞）。update/pause/cancel 失败键统一
`message`(+job_id)；create 失败键是 `error`（与读一致）。

## 2026-08-11 — 补齐 create/pause/cancel（job 3 写全迁 seam）

`create_job_from_args` / `pause_job_from_args` / `cancel_job_from_args` 落地，job 工具
不再持 `get_db_client_fn`——3 写全走 [[store]] AgentDataStore seam，mcp 容器可 strip
`DB_PASSWORD`。要点：

- **`setup_mcp_llm_context` 从工具搬进 `create_job_from_args`**。它跑相似标题 embedding
  dedup，需要 owner LLM 配置上 ContextVar，而那次配置加载**读 DB**。所以必须在**谁持 DB
  谁跑**：本地 DirectStore=mcp 进程、云端 seam 路由=backend（mcp 无凭据、加载不了配置）。
  留在工具里会在云端炸（无 DB）。W1 结构化错误兜底（LLMConfigNotConfigured=几乎总是猜错的
  agent_id→可行动重试；其余→结构化 dict）随之搬进 helper，从不抛。
- pause/cancel 沿用 update 的 **no-existence-oracle**（别的 agent 的 job = "not found"，
  比旧工具的 "does not belong to agent X" 更安全）；cancel 的实体解绑是 best-effort（记日志、
  绝不回滚已观察到的 cancel）。
