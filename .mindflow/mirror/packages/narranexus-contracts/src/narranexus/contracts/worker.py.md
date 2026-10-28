---
code_file: packages/narranexus-contracts/src/narranexus/contracts/worker.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `backend.workers` 位的契约

`WorkerSpec(name, factory, host, stable_after_s)` 与 `run_worker_supervisor.WorkerSpec` 同构（工厂每次
(重)启动被调一次、返回 `run`+`stop` 的句柄），但是契约层自己的类型：宿主把插件 worker 命名为
`<owner>:<name>`，所以名字里禁止 `:`。`host` 只有 `workers`（默认，supervisor 进程）与 `backend`
（必须与 API 进程共存的 lifespan 任务）。
