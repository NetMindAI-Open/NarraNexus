---
code_file: frontend/src/types/api.ts
last_verified: 2026-07-20
stub: true
---

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

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

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

背景见 `reference/self_notebook/specs/2026-04-21-job-timezone-redesign-design.md`。前端不再感知 UTC——所有时间都以 "local + tz" 配对流动。

## 2026-06-16 — CreateAgentRequest.team_id (#43)

`CreateAgentRequest` 新增可选 `team_id`：在某个 team 下点 "Add agent" 时带上，后端据此把新 agent 归入该 team。

## 新人易踩坑

- 不要"为了方便前端排序"悄悄加回 `next_run_time: string`。β 之间不可比较（跨时区 job 无全序），排序/筛选的"时间 cursor"只存在于后端 α 里
- 如果后端 response 新增时间字段，**必须**同步配 `_timezone` 字段，不能只有时间主体
