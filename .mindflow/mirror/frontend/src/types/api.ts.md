---
code_file: frontend/src/types/api.ts
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `FileListResponse.truncated?`

对齐后端 `FileListResponse.truncated`（workspace 目录树触到节点/深度上限、且确有可见条目
被略掉时为 true）。可选：mock 与旧后端不带它，消费方（`FileUpload`）按 `=== true` 判断。

## 2026-10-14 — `FactoryBuiltin.excluded`

Required flag (the backend always sends it; no old-payload shim) from `GET /api/plugin-factory` `builtins[]`: the running distribution leaves the builtin out (never loaded);
`enabled` is false for such rows and cannot be changed.

## 2026-09-10 — `JobStatus` / `JobQueueStatus` / `QueueCounts` + `paused_spend_cap`

后端 `JobStatus.PAUSED_SPEND_CAP`（日花费上限暂停，B-14）首版没登记到前端：`statusVisual()`
兜底成 pending 的样子，`STATUS_ORDER` 没有它于是无 filter chip / meter 段，看板计数没有这一列。
三个类型同批加入；`Record<JobStatus,…>` 的穷尽约束让 [[jobStatusVisuals.ts]] 与
`lib/mock/fixtures.ts` 在 tsc 下必须跟着补。

## 2026-09-09 — `*CredentialData.disabled_reason?`（B-28）

Telegram / Slack / Discord / WeChat / Narramessenger 五个凭据类型与泛型 `ChannelCredentialView` 都加了可选 `disabled_reason`（后端
`to_public_dict` 新增的公开字段）；旧响应无此键时 UI 不显示原因。Lark 用自己的 `auth_status` 机制，不带。

## 2026-09-09 — `CircuitBreakerStatus` gains `'probing'`

[[circuit_breaker]] 新增半开态 PROBING（GitHub #117），`GET
.../circuit-breaker` 现在会把它原样透传成 `cb_status`。补进联合类型，避免
消费方（[[wsCircuitOpen]] 的轮询判定）以为只有三个值。

## 2026-09-07 — `PluginChannelRow`

`GET /api/plugins/channels` 的一行：宿主 `ingress.channels` 注册表的数据形态，
供 [[registerBuiltinChannels]] 建 Channels 区的行用。

## 2026-09-03 — `BoundChannel`；删 `AgentModelOverview`

`AgentInfo.bound_channels: BoundChannel[]`（`{channel, active}`）。`agent_framework` /
`model` 注释改为实情：只对自己的 agent 存在，缺失渲染 `—`。

## 2026-08-30 — `EventLogTimelineEntry.monologue?: boolean`

镜像后端 `api_schema.EventLogTimelineEntry` 同名字段（见 [[api_schema]]）。
只对 `thinking` 条目有意义：该块是 NexusPower 独白而非 provider CoT，回放时
按「进度」档渲染。

**这里是 bool，不是子集文本**——后端已经把档位判完并按档切块（一个条目一个
档）。实时 WS 帧走的是另一套（`AgentThinking.monologue` 是 string 子集，见
[[messages]]）。存量行没有该字段 → 普通 thinking。

## 2026-08-28 — 插件安装类型（`PluginStatus` / `PluginInstallEvent`）

`PluginStatus`（installed/version/target_version/update_available/logged_in/
size_hint/busy）镜像后端 `backend/integrations/plugins` 的插件状态；
`PluginInstallEvent` 是判别联合（`done: false` 的进度帧 vs `done: true` 的终帧
带 `ok/error/status`），消费方是 `api.installPlugin` 的 ndjson 逐行解析与
[[PluginsSettings]]。`PluginId` 收窄成 `'claude_code' | 'codex_cli'`——只有这两
个框架背后有一个要装的本地插件，nexus_power 没有对应插件。

## 2026-08-26 — bulk slot 类型

`SlotOverrideStats`（各槽覆盖数 + total_agents，喂确认框）与
`AgentModelOverview`（per-agent 每槽 effective model + inheriting，喂 Dashboard
chip），经 `@/types` barrel 暴露给 `lib/api.ts`。

## 2026-08-27 — `AgentInfo` 三个新字段 + `QueueCounts` 补三个状态

- `AgentInfo` 加 `agent_framework?` / `model?` / **`bound_channels: string[]`**。
  最后一个**故意是必填**:Dashboard 之外还有 mock fixtures 和
  [[../hooks/useCreateAgent.ts]] 的乐观插入在手工构造 `AgentInfo`,设成可选的话
  这些地方会静默漏字段、目录里整列显示 `—` 而没人发现。必填让 `tsc` 当场点名。
  语义见 [[../../../src/xyz_agent_context/schema/api_schema.py]]。
- `QueueCounts` 加 `cooling` / `paused_no_quota` / `blocked_failed`。后端算 `total`
  时一直包含这三项,这里缺字段就意味着**分项之和永远对不上 total**。

本文件是 Pydantic 类型的手工复刻,没有生成器也没有契约测试(见下方既有 Gotcha)。
后端改了这两个模型,这里不改的话 `tsc` 不会红——TS 对多出来的运行时字段是宽容的。
## 2026-08-19 — `UpdateAgentResponse` 补两个可选字段

`name_clash_with?: string | null` 与 `identity_record_updated?: boolean | null`,
对应后端 [[api_schema]] 的同名字段。

**两个都必须是 optional + nullable**:它们是 additive 字段,老后端的响应里根本
没有这两个 key,而 mock(`frontend/src/lib/mock/index.ts`)也不带——标成必填会让
mock 和旧响应双双编译不过。

`identity_record_updated` 是**三态**,不是布尔:`null` = 这次调用对身份记录无事
可做(没改名、也没发现过期记录),`false` = **发现了但没修成**。把两者折成一个
布尔,"这个 agent 没问题"和"这个 agent 还坏着"就分不开了,而后者是事故本体。

## 2026-08-19 — `SubscriptionStatus.payment_method` / `SubscriptionPlan.usd_monthly_price`

`payment_method` 是**判别字段**，不是装饰：一次性订阅与已取消的卡订阅在其余每个字段
上都一模一样。可选，因为早于 nexus 账号的订阅都没有它 —— **缺失即卡**。

`usd_monthly_price`（一个月多少钱）和 `monthly_grant_usd`（一个月给多少额度）
今天数值相同，也正因如此才分开：一次性总价必须按前者算，否则任一边变动都会静默
算错 12 个月的结账金额。

## 2026-08-18 — 支付方式与汇率报价的类型

`RechargePaymentMethod`（`default | alipay | wechat`）与
`SubscribePaymentMethod`（`stripe | alipay | wechat`）**故意分成两个类型**：同一条
"刷卡"通道，上游在两个接口里的拼写就是不一样的，合并成一个联合类型会让调用点在
编译期看起来合法、运行期被上游 400。

`FxQuote` 的每个字段都是 `string` 且都可选 —— 金额用文本传（避免浮点漂移），
且后端逐字代理上游、不做 schema 校验，所以**部分字段缺失的 200 是可能的**，
读取点必须可选链 + 兜底。与 [[FeeInfo]] 同一条规矩。

`RechargeCheckout` / `SubscribeCheckout` 多了 `charge_currency` / `charge_amount`
/ `fx_rate`：**只有非美元（微信）才有**。`charge_amount` 是银行真正划走的钱，
用户拿到的额度仍是他请求的那个美元数 —— 两者不是一回事，别混用。

## 2026-08-11 — ApiResponse 加可选 `message`

一些后端信封带机器码 `error` + 人话 `message`（如 lark unbind 返回
do_unbind 原信封 `error:"no_credential"` / `message:"No Lark bot bound…"`）。
`message?` 让 [[LarkConfig]] 等优先显示人话。

## 2026-08-07 — CancelRunResponse

`already_settled` 表示该 run 在请求到达时已经终态 —— 服务端此时**不会**
落旗标(否则那个旗标会成为该 agent 下一个 run 的陷阱),前端据此知道
"没什么可停的",而不是当成失败。

## 2026-07-30 — Cost*/EventLogMeta 镜像缓存两桶字段

跟随后端 api_schema 同日改动:`CostModelBreakdown` / `CostDailyEntry` /
`CostRecord` / `CostSummary` / `EventLogMeta` 加缓存读/写字段。**全部可选,
消费方必须 `?? 0`**:字段落地当天就踩过一次——新前端(Vite 热更)对着未
重启的旧后端,响应里没有这些键,undefined 进求和直接渲染成 "NaNM"。
语义:`input_tokens` 只是满价未缓存桶,展示层求和必须把三桶都加上,
否则缓存热的 agent 输入侧少报两个数量级。

## 2026-07-30 — TriggerConfig 字段名对齐后端

`TriggerConfig` 原来声明 `cron_expression` / `trigger_type`,与后端
`schema/job_schema.py` 的 `TriggerConfig`(透传字段 `run_at` / `cron` /
`interval_seconds` / `timezone` / `end_condition` / `max_iterations`)不符,导致
消费方读 cron 一直读到 undefined。改为与后端一字不差,供「编辑执行时间」正确回填。

## 2026-07-23 — QuotaMeResponse.free_tier

`QuotaMeResponse` 的两个 enabled 分支加可选 `free_tier?: {active, model}`
（`FreeTierLock`）。免费额度有余量时运行时锁死系统模型、忽略用户自有 slot 编辑，
`active` 让 [[ModelDefaultsSettings]] 渲染诚实 banner，`model` 是锁定时真正运行的模型。
后端见 [[quota]] 的 free_tier 块。

## 2026-07-20 — RoomMessage.attachments

`RoomMessage` gained `attachments?: BusAttachment[]` so inbox message cards can render
bus files (see [[BusAttachmentList]]).

## 2026-07-18 — FeeInfo.metrics 加 subscription_credit;两处注释改闩锁语义

`FeeInfo.metrics` 新增 `subscription_credit?: string`（Pro 赠额余量，跨周期
累积，dev 实测验证），并给 `monthly_free_credit` 加警示注释（dev 返回 0.50
与真实 $19/期不符——**不得**当周期分母用，面板用 proPlan.monthly_grant_usd，
疑点见 self_notebook todo）。`QuotaMeResponse.prefer_system_override` 注释从
"User's choice"改为耗尽通知闩锁只读语义（review 抓出的漏网）。

## 2026-07-15 — MCP 类型加 headers

`MCPInfo.headers` 为掩码后的值（后端 `_masked_headers`）；Create/Update 请求
的 `headers` 为明文提交、Update 语义=出现即整组覆盖。

## 2026-07-13 — Agent 实时层熔断器接入

新增 `CircuitBreakerStatus` 与 `AgentCircuitBreakerResponse` 类型（熔断状态查询响应）。

## 2026-07-10 — ClearHistoryResponse expanded

`ClearHistoryResponse` now carries `scopes` + per-target counts
(event_stream, chat memory, chat instances, agent_messages, memory_rows,
artifacts), disk-removal booleans and `disk_errors[]`, mirroring the backend
`WipeResult`.

## 2026-07-09 — AgentSlotView / AgentSlotEffective

Types for the per-agent LLM config endpoint: ``AgentSlotEffective`` (flat
provider/model/thinking/reasoning_effort, +agent_framework on the agent slot)
and ``AgentSlotView`` (inheriting / effective / override / owner_default per
slot). Consumed by [[api.ts]], [[ComposerModelBadge]], [[AgentLlmConfigPanel]].

 
## 2026-07-05 — recharge types (Phase 4, module E)

Added RechargeCheckout/RechargeResponse ({recharge_id, session_id, checkout_url, status}) and
RechargeStatus/RechargeStatusResponse ({status: pending|succeeded|failed}) for the top-up flow.

## 2026-07-03 — BusFailureItem/BusFailuresResponse + NoticeItem/NoticesResponse

Types for the upstream #52 recovery surface.

## 2026-05-27 — LarkErrorDetail (translator output)

`LarkBindResponse` now optionally carries `error_detail: LarkErrorDetail`
on failure. Field names match the backend `_lark_error_translator`
`ErrorTranslation` dataclass 1:1 so the JSON round-trip works without
adapters. `LarkConfig.tsx` renders this as a structured card with
title/message/action_hint/console_url; falls back to plain `error`
when absent.

## 2026-05-14 — FileInfo becomes a recursive tree node

Mirrors the backend `api_schema.FileInfo` change. The flat
`{filename, size, modified_at}` shape became a recursive tree node:
`{name, path, is_dir, size, modified_at, children?: FileInfo[] | null}`.
`FileListResponse.files` renamed to `tree`. `FileDeleteResponse.filename`
renamed to `path` because the backend DELETE accepts nested relative paths.

## 2026-05-13 — Phase C: ActiveRunInfo + AgentInfo.active_run

Adds the frontend mirror of the backend ActiveRunInfo type so the
GET /api/auth/agents response carries enough metadata to render the
"Running" indicator across tab reloads / devices. Field set matches
`xyz_agent_context.schema.api_schema.ActiveRunInfo` exactly.

# types/api.ts

## 为什么存在

前端与后端通信的全部 TypeScript 类型定义，对应后端的 Pydantic 响应模型（`src/xyz_agent_context/schema/api_schema.py`）。任何 API route 返回的数据形状在这里都要有对应 interface。

## 2026-04-21 · v2 时区协议

`Job` 和 `DashboardPendingJob` 接口里的 UTC 字段全部替换为 β：

```ts
// removed:
// next_run_time?: string;
// last_run_time?: string;

// added:
next_run_at?: string;
next_run_timezone?: string;
last_run_at?: string;
last_run_timezone?: string;
```

背景：job 时区重设计（2026-04-21）。前端不再感知 UTC——所有时间都以 "local + tz" 配对流动。

## 2026-06-16 — CreateAgentRequest.team_id (#43)

`CreateAgentRequest` 新增可选 `team_id`：在某个 team 下点 "Add agent" 时带上，后端据此把新 agent 归入该 team。

## 新人易踩坑

- 不要"为了方便前端排序"悄悄加回 `next_run_time: string`。β 之间不可比较（跨时区 job 无全序），排序/筛选的"时间 cursor"只存在于后端 α 里
- 如果后端 response 新增时间字段，**必须**同步配 `_timezone` 字段，不能只有时间主体

2026-08-19：`TriggerConfig` 增 `end_at?: string`（scheduled 地平线，镜像后端
`job_schema.TriggerConfig.end_at`）；`NetmindLoginResponse`/`CreateUserResponse`
增 `guide_agent_provisioning?: boolean`（服务端 kill-switch 回显，见
api.ts.md 的 coachmark 门控段）。

## 2026-08-27

`OnboardingProgress.landing_completed` mirrors the backend field the first-run
flow writes (see `api_schema.py`).

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
