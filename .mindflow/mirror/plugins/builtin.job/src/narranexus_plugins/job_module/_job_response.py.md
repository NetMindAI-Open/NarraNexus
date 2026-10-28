---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/_job_response.py
last_verified: 2026-04-21
stub: false
---

# _job_response.py

## 为什么存在

v2 时区协议（spec 2026-04-21）要求 MCP retrieval tool 返回的 Job 视图**完全剥离 UTC 字段**——LLM 只应看到用户本地时间 + IANA 时区标签。这个文件把"Job → LLM 可见 dict"这一步从各 retrieval tool 里抽出来成为唯一入口 `job_to_llm_dict(job)`，任何 tool 返回 Job 信息都必须走这里。

## 上下游关系

- **被谁用**：`_job_mcp_tools.py` 里三个 retrieval tool（by_id / semantic / keywords）+ `job_retrieval_by_id` 在 `job` 字段上拼 spread
- **依赖谁**：`schema.job_schema.JobModel`（通过 TYPE_CHECKING 只做类型注解，运行时不耦合）

## 设计决策

**显式排除 `next_run_time` / `last_run_time`**：这两列是 poller 内部字段（UTC 物理瞬间），暴露给 LLM 会诱导它按 UTC 解读并产生时区混乱。函数体是"白名单"式构造而非 `model_dump + del`——确保未来 JobModel 加了任何新 UTC 类字段不会被默认透传。

**`trigger_config.model_dump(exclude_none=True)`**：LLM 看到的协议视图里 timezone 必然存在（v2 validator 强制）；过滤 None 让 JSON 更干净。

## Gotcha / 新人易踩坑

- 新增 job retrieval tool 时**不要**手动构造 job dict——一律 `job_to_llm_dict(job)` 拼 spread，否则漏字段或漏排除 UTC 会复活时区 bug。
- β 列名 `next_run_at_local` 在 Job 实体里，但 LLM 层暴露名为 `next_run_at`（无 `_local` 后缀）。命名差异是有意的：DB 层强调"naive local"物理事实，LLM 层强调"这就是用户意义的那个时间"。
