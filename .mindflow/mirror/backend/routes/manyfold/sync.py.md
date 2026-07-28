---
code_file: backend/routes/manyfold/sync.py
last_verified: 2026-07-28
stub: false
---

# sync.py — Manyfold managed-trigger surface

## 为什么存在

Manyfold cloud sandbox 会在空闲时挂起整个 VM。进程内轮询器（job_trigger）
和常驻 IM 连接（run_channel_triggers）在挂起期间是死的：定时任务静默错过、
IM 消息丢失；反过来若连接把 VM 钉在常醒状态，托管成本又失去意义。解法是
把「钟」和「耳朵」交给平台：run.sh 在 `NEXUS_EXTERNAL_TRIGGERS=1` 时给
worker supervisor 传 `--exclude jobs,channels`（dev 已把四个 worker 进程
合并成一个 supervisor，所以门控落在 worker 选择上而不是进程启停上），
`poller` 和 `bus` 是 sandbox 本地的、照常运行。Manyfold 用自己的
automations（镜像闹钟）和 channel 连接接管，事件发生时按需唤醒本容器执行。

本文件把所有 Manyfold 侧需要的配合面收敛在一处（Owner 关切：开源仓库不
在核心模块里散布平台耦合代码）：

1. `GET /manyfold/jobs` / `GET /manyfold/channels` — 只读 inventory，
   Manyfold pull 后在**它那边**做全部映射/翻译。channels 端点解码 secret
   是有意的：仅 gateway token 之后可达，Manyfold 需要原始 bot 凭据去开
   替代连接（落库时其侧加密）。slack 行照常返回但 Manyfold 会跳过
   （Socket Mode 凭据没有 Events-API 需要的 signing secret）。
2. `config_change_webhook_middleware` — response 侧观察者：job/channel/
   provider 路由的 2xx 写请求后 fire-and-forget POST
   `MANYFOLD_SYNC_WEBHOOK_URL`（500ms 合并窗口）。**永不 raise、无重试**：
   Manyfold 把任何 notify 都当"全量拉一遍"，丢一条只是延迟到下一次
   turn/boot pull。`/api/providers` 也算 jobs 变更——provider 变更会
   edge-trigger 恢复 PAUSED_NO_QUOTA 的 job。
3. `parse_run_job_control` + `execute_job_once` — Manyfold 镜像闹钟到期
   时发起 chat turn，prompt 恰好是 `[[nx:run_job <job_id> v1]]`（严格全
   匹配，带任何多余文字都当普通对话）。openai_compat 识别后转到这里，
   复用 `JobTrigger._execute_job`（try_acquire_job CAS 防双跑、finalize
   推进 next_run_time/状态——与 poller 拾取完全同副作用），随后 bounded
   drain 顺带执行醒着期间到期的其他 job。

## 上下游

- 被 `backend/main.py` 在 `ENABLE_MANYFOLD_API=1` 块内注册（router +
  middleware）；middleware 注册在最后 → Starlette LIFO 下最外层，观察到
  最终 status code，对 OPTIONS/非 2xx 完全透明。
- 被 `backend/routes/openai_compat.py` import（parse + execute）。
- 依赖 `JobTrigger` 的三个方法：`_execute_job`（执行体）、
  `_rearm_cooled_jobs`、`_resume_eligible_no_quota_jobs`（维护双通道）。
  poller 关掉后这两个维护 pass 没人跑了，所以每次 run_job dispatch 先跑
  一遍：COOLING 的重臂纯粹是时钟问题（镜像闹钟正好在 cooldown_until 触
  发）；NO_QUOTA 的主恢复路径仍是 provider 路由的 edge-trigger，这里只是
  backstop。
- 六个 credential manager 只被 import 调 `list_active()` /
  `get_active_credentials()`，零修改。

## 设计决策

- **drain 的边界限制的是"再拾取多少个"，不是单 job 时长**（铁律 #14）：
  `_DRAIN_LIMIT=5` 条、预算 300s、窗口 30s（每执行一个成功 job 就顺延，
  接住 module_poller Path B 刚激活的依赖链）。job 自己跑多久不设限。
- run_job dispatch **不做 env 门控**：端点本身已有 gateway token 鉴权，
  `try_acquire_job` 兜底双跑；回滚后残留的镜像闹钟打进来直接执行也比把
  控制文本喂给 LLM 好。
- webhook 的 4 个 env（`MANYFOLD_SYNC_WEBHOOK_URL/TOKEN`、
  `MANYFOLD_RUNTIME_ID`、超时 `MANYFOLD_SYNC_WEBHOOK_TIMEOUT_S`）直接
  `os.environ.get`，与既有 `MANYFOLD_GATEWAY_TOKEN` 同惯例，不进
  settings。缺任何一个 → middleware 透传，零开销。

## Gotcha

- `/manyfold/jobs` 排除终态（completed/cancelled/failed）并有 500 行
  cap（超限打 warning）——Manyfold 按"payload 里没有 = prune 镜像"语义
  消费，所以**任何过滤条件的变更都是对 Manyfold 的语义变更**。
- webhook 的 done-callback 必须 retrieve exception（教训 #2：裸
  create_task 的异常静默丢失且报 "exception never retrieved"）。
- message_bus 运行中经 MCP 建的 job 没有 HTTP 请求经过 middleware，
  webhook 不会发——依赖 Manyfold 的 turn 结束 pull / boot pull 兜底，
  这是已知 v1 边界。
