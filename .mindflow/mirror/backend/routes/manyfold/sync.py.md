---
code_file: backend/routes/manyfold/sync.py
last_verified: 2026-09-07
stub: false
---

## 2026-08-26 — `retag_managed_input`：盖章之后重渲 tag 行

`build_inbound_run_context` 必须先渲染 tag 才能拼出 `run_input`，但 tag 上
有些字段**要等渠道自己的 trigger 看过这一轮才知道**——`is_agent_peer` 是
第一个这样的字段（只有 trigger 层知道各平台的身份约定）。

顺序是：`:612` 渲染 → `:639` `before_run` 把 `is_agent_peer` 盖进
`trigger_extra_data["channel_tag"]` **字典** → `:702` 把**早就定型的**
`run_input` 送进 agent。**字典被改了，模型读的那个字符串没有。**

后果正好落在这个信号最该生效的地方：`MatrixTrigger.is_agent_peer` 是全仓
唯一能真答出这个问题的实现，而 NarraMessenger 就跑在托管模式下。于是托管
A2A DM 里，模型拿到的 tag 与人类对话逐字节相同，而 DM 协议里那条
「若标记为 `agent sender`」是一条**永远走不到的分支**。

修法：**替换**那一行，不新拼一行（两行 tag 比一行过时的更糟），且仍然只走
`ChannelTag.format()` 这一个渲染定义——在托管侧手写 `agent sender` 就退回成
「N 份手抄」了。顺带让 `ChannelTag.from_dict` 从死代码变成有调用点。

**用 `user_input` 重建整串**，而不是对渲染好的字符串做手术。第一版切首行、
判断它以 `[` 开头 `]` 结尾——`sender_name` 是平台转发的 display name，
`_ctx_str` 当时只削首尾，一个**名字里带换行**的昵称就会让 `tag.format()` 自己
跨两行，于是首行不以 `]` 结尾、重渲被跳过、标记**静默消失**。而希望这个标记
消失的正是对面那个 agent，它恰好控制自己的昵称。

配套把 `_ctx_str` 改成折叠内部空白：tag 是**单行协议**，带换行的昵称在别处
（chat history）也是雷。

**注意折叠面比 tag 大**：`_ctx_str` 同时喂着 `room_id` / `source_message_id`
/ `thread_id` / `reply_token`，而 `trigger_id` 由 `source_message_id` 拼出来
并进审计行。这些都是 ID，内部带空白本身就是病态输入，折叠对它们无害——但
**如果哪天有渠道的 ID 合法地含空白，这里要先分字段处理再动**。

无 `channel_tag` 的纯 Manyfold turn 原样返回。

## 2026-08-10(review 修)— env 委托 + 全败还原批次

`_webhook_env` 改为委托 integrations/manyfold_outbound 的
`manyfold_runtime_env()`(身份对唯一解析点),并**自带**
`webhook_url` 非空这道 notify 腿专属的门(净行为不变:仍三者齐全
才发);`_flush_pending` 全败
后 `_pending_kinds.update(kinds)` 还原整批——否则重试窗口(~31s)内
被并批吸收的 kinds 随注定失败的批次一起消失,比无重试时代更糟
(那时它们会留在 _pending_kinds 等下一个 task)。notify 是"pull
everything"语义,晚到重发无害。

## 2026-08-10 — A1 `agent_managed_reply` 显式下发 + A3 notify 退避重试

channels inventory 每行 `config` 后处理注入
`agent_managed_reply`(bool,事实源 = integrations/manyfold_outbound
的 env 声明)。**显式 false 是关键**:平台 mapper 对缺失键按 managed-ON
兜底(#504),缺键 = 渠道在某次不可预测的 pull 后突然翻托管;一个
post-pass 循环保证未来第七个 provider 不可能漏键。

`_flush_pending` 加退避重试(`_NOTIFY_RETRY_BACKOFF_S` = 1s/5s/25s):
bind 完丢 notify 是最疼的场景(用户直接去 IM 等,turn-final 扳机
永不触发,平台周期 reconcile 只扫醒着的沙盒)。重试间隙到达的
kinds 并入当前批(flush task 单飞,没人会另行捡起它们);全败后仍
raise 给 done-callback 记 warning——never-raise 面向调用方不变。

## 2026-08-04 — 契约值归一化 + lark enabled 收敛(review)

chat_type 统一 .lower()(平台 TS 传 "GROUP" 不再漏过静默判定);
is_mention 走 `_ctx_flag`(字符串 "false"/"0" 不再因 bool() 变真——
那会让非 @ 群消息被当成 @,agent 重新闯群闲聊)。lark 行 enabled 改用
`cred.receive_enabled()`(get_active_credentials 已滤 is_active,语义
单一居所)。
## 2026-08-03(补) — trigger_extra_data 恒带 `managed_ingress: True`

渠道 turn 的 extra_data 加显式 managed 标记——原本与原生 trigger 的
extra_data 形状难以区分,而 narramessenger 等模块需要按来源切换回复
指令(narra_reply→narra_send)。原生路径不带此键,语义即 False。

## 2026-08-03 — managed-IM 入站分流(model B 消费端回归 + 契约扩展)

新增 `_PROVIDER_WORKING_SOURCE` + `build_inbound_run_context()`:把
openai_compat 转来的 `channel_provider/channel_context` 翻译成
`(working_source, ChannelTag 前缀输入, trigger_extra_data)`。历史注:该
消费端最早实现于 feat/manyfold-cloud(PR #118),#172 rebase 时丢失,
本次按 v1 契约(spec 2026-08-03)重建并扩展——新增 optional 字段
chat_type / thread_id / reply_token / is_mention / attachments 的透传。
关键语义:未知 provider(含 matrix 旧映射、slack)→ 原样 MANYFOLD 裸
turn,零行为变化;`trigger_id` 走原生 `{channel}_{message_id}` 约定
(平台已去重,这里只是 trace 身份);平台原始附件字典走
**`manyfold_attachments`** 键——绝不能直接放 `attachments`,那个键会被
context_runtime 的 marker 管线按原生 Attachment schema 消费,未转换的
平台字典会产出垃圾 marker(转换归 ingress 执行体,阶段 E)。所有
context 值经 `_ctx_str` 强制 str 化(平台是 TS,int/None 常见)。

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

## 2026-09-04 · services + host hooks (batch 3c.6)

`/manyfold/channels` collects rows from every channel builtin through `onWillExportManagedChannels` (provider order pinned by `_PROVIDER_ORDER`); `execute_job_once` runs through builtin.job's `jobs.run_once` service (`RunJobOutcome` now subclasses `contracts.job.JobRunOutcome`; `jobs_unavailable` when the builtin is disabled). The route imports no channel or job module.

## 2026-09-04 · channels as descriptors (batch 4a)

`_provider_working_source()` resolves an IM provider to its inbound WorkingSource through `ingress.channels` (credentials-only channels and unknown names → None) instead of the hard-coded six-entry map.

## 2026-09-04 · registry-driven order and config paths (batch 4e)

`_provider_rank` orders the channel payload by the descriptors' `ui.order` (unknown providers last); `_channel_path_prefixes()` = `/api/channels` (where every channel binds since 4d.3 — the old tuple would have missed every bind) + each registered channel's own router, so a plugin channel's writes notify Manyfold too.

## 2026-09-07 — 删除死的 module_system 导入（round-2 P2-C1）

注册只在 boot；未 boot 的进程没有渠道即回退为普通 MANYFOLD turn。
