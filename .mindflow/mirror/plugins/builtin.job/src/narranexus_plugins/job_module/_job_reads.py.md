---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/_job_reads.py
last_verified: 2026-08-10
stub: false
---

# _job_reads.py — 共享的 job 读实现

## 为什么存在（PR-8）

job 的三个读工具（job_retrieval_by_id / _semantic / _by_keywords）迁 AgentDataStore
seam。它们本就走 `JobRepository`（方言安全、无裸 SQL），迁移只是把工具体提到这里，
让 seam 的 DirectStore 与 backend [[jobs]] 孪生路由调**同一实现** → byte-parity（单
一函数，不是两份手抄）。`fetch_job_by_id` / `search_jobs_semantic` /
`search_jobs_by_keywords` 各自返回完整 dict、从不抛异常。

## agent 归属

三个都按调用方 agent 限定：by_id 显式 `job.agent_id == agent_id`（否则 Access
denied）；两个 search 把 `agent_id` 传给 repo，结果天然是自己的。

## 与工具逐字对齐的细节

- semantic 与 by_keywords 的 invalid-status 文案**不同**（semantic 带 "Valid
  values: ..."，keywords 只 "Invalid status: X"），各自内联，勿合并。
- semantic 的 valid 列表**由 `JobStatus` 枚举派生**（预审改：不再是工具里那份只列
  5 个状态的硬编码，与 `backend/routes/jobs.py` list_jobs 的 `[s.value for s in
  JobStatus]` 同源，JobStatus 新增状态时自动跟上）。这是 agent 可见返回值的有意变更。
- 三个 helper 最内层用 `logger.exception` 保留 traceback（store.py 外层 wrapper 用
  warning 是因为它只兜已知的 `_db()` 失败）。
- by_id 在 `job_to_llm_dict` 之外多带 process/last_error/created_at/updated_at。
- by_keywords 对 description >200 截断加 "..."。
- `search_jobs_semantic` 是 BM25（向量退役），工具名保留只为 agent 面契约；工具里
  原来的 `setup_mcp_llm_context` 已删（该 path 不用 LLM）。
