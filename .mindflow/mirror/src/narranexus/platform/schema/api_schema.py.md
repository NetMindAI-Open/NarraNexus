---
code_file: src/narranexus/platform/schema/api_schema.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `FileListResponse.truncated`

workspace 目录树接口有了节点数/深度上限（[[files]]），超限**且确有可见条目被略掉**时
`truncated=True`；前端 `FileUpload` 据此在树下提示"部分列表"（`awareness.workspace.partialListing`）。

## 2026-09-03 — `BoundChannel` + `AgentInfo.bound_channels` 改为对象列表

`{channel, active}`：active=False 是「配置了但开关关着」。`agent_framework` / `model`
的注释改为实情——只为调用者自己的 agent 解析，别人的公开 agent 为 None。

## 2026-08-30 — `EventLogTimelineEntry.monologue: Optional[bool] = None`

`thinking` 条目的档位：该块是 NexusPower 自己的 assistant 文本（独白）而不是
provider CoT。**纯加法，没有回填、没有迁移**（铁律 #2/#6）——`None` 覆盖
CoT、非 NexusPower driver、以及该字段存在之前写入的存量行。

**为什么是 `Optional[bool]` 而不是 `bool = False`**：这个模型是
thinking / tool_call / tool_output / native_output 的**联合形状**，其余
「只对某一种 type 有意义」的字段（`reply_via` / `tool_name` / `tool_output`）
全都是 `Optional[...] = None`。第一版写成非可选 bool，于是每个 tool_call 行
都会序列化出一个语义上无意义的 `"monologue": false`（几百步的 log 就是几 KB），
而且和前端 [[api]] 已经声明的 `monologue?: boolean` 两侧不齐。

档位在 [[chat_history_timeline]] 判定（子集 == 并集才算独白，混档回落 False），
这里只是承载。

## 2026-08-27 — `AgentInfo` 加了三个目录字段

`agent_framework` / `model` / `bound_channels`,给 Dashboard 智能体目录的
Framework / Model / Channels 三列供数,由 [[../../../../backend/routes/auth.py]]
的 `/api/auth/agents` 批量投影(不是每行一次 `/llm-config`)。

- `agent_framework` / `model` 是**生效值**,不是原始配置:per-agent 覆盖优先于
  owner 的默认。`None` 表示这个 agent 没有任何 slot 配置。
- `bound_channels` 用 `Field(default_factory=list)` 而不是 `= []`——可变默认值在
  Pydantic 里虽然会被拷贝,但显式 factory 是本仓库的写法,别改回裸列表。
  **语义是"绑过",不是"当前可用"**:凭证行存在即计入,禁用状态也算。别拿它当
  健康检查用。
- 别人拥有的公开 agent 永远是空列表(后端只对 owner 自己的 agent 查渠道表)。
  前端不要把"空列表"理解成"这个 agent 没接渠道"。

TS 侧的 `AgentInfo`([[../../../../frontend/src/types/api.ts]])是手工复刻,加字段
两边都要动;那边 `bound_channels` 是**必填**,故意的——逼每个构造 AgentInfo 的地方
(mock fixtures、useCreateAgent 的乐观行)显式想一下这个值。
## 2026-08-18 — `UpdateAgentResponse` 多两个字段

改名事务（[[_overview]]）算出来的两件事，原来到了 HTTP 层就被丢掉：

- **`name_clash_with`** —— 同 owner 下已经有别的 agent 叫这个名字。**不拦**：
  把一个名字从一个 agent 转给另一个是 owner 会故意做的事，拦掉是错的；**静默**
  才是错的——两个 agent 同时应一个名字正是 P1 段02 ① 的起点。agent 自己的工具
  一直在报它，界面改名却是唯一会悄悄发生的入口。
- **`identity_record_updated`** —— 本次改名有没有把 Awareness 身份记录也写成功。
  `None` 表示这次没改名。改名本身**已经落地**（为一个确实存过的名字报失败是更糟的
  谎），但「列改了、记忆没跟上」正是深圳那次事故的状态，它不该只存在于一条
  `docker restart` 就会抹掉的容器日志里（CLAUDE.md 事故教训 #5）。

两个都是**附加字段**，老客户端忽略即可。更强的形态是写一行 DB 审计事件（教训 #5
的原话），那要建表、走迁移 SOP（铁律 #6），记在
`reference/self_notebook/todo/` 待 Owner 定。

## 2026-08-17(补)— `_StrippedText` 迁到 entity_schema

本文件原来自己定义 `_strip_if_text` / `_StrippedText`。manyfold 的两个写边模型
(`backend/routes/manyfold/agents.py`)也需要同一个行为,而它们**不能** import
api_schema(成环),所以定义搬到 [[entity_schema]] 的 `StrippedText`,本文件深引
并别名回 `_StrippedText`。

深引不是疏忽:`xyz_agent_context.schema` 门面反过来再导出本文件的模型,本文件引
门面就成环 —— 与本文件引 `AGENT_TEXT_MAX_LENGTH` 的方式一致,已在 import 处注明。

## 2026-08-17 — Create/UpdateAgentRequest 的长度上限改为量归一后的值

两个模型的 `agent_name` / `agent_description` 类型换成 `_StrippedText`
(`Annotated[str, BeforeValidator(_strip_if_text)]`),`max_length` 不变。

原来 cap 量的是**原始串**,于是 `"x"*255 + " "` 在 HTTP 侧 422、在 agent 侧
([[_awareness_writes]] 对 strip 后判长度)通过 —— 同一个输入两条路径两个答案,
正是这一轮在消灭的形态。归一后再量,两边验收集合一致。

`None` **必须原样穿过**:更新路径上 `None`="没传这个字段"、`""`="清空这个字段",
只有这一点区分二者,collapse 掉就没法清空描述了。`_strip_if_text` 因此只对
`str` 动手,其余交给 pydantic 自己报错。

422 的契约没动(`tests/backend/test_agent_request_length.py` 钉着四个写边模型
统一 422,是 2026-07-23 补 #71 时立的)。本次给它补了三条:尾随空白不该把合法值
顶过上限、真超长 strip 完仍拒、`None` 不被变成 `""`。

> 走过一次弯路:先把 `UpdateAgentRequest` 的 `max_length` 摘了、改在路由里判,
> 结果打破了那条四模型统一的契约(Update 变成 200+success=false 而 Create 仍 422),
> 被上面那个测试当场抓住。BeforeValidator 才是既保 422 又对齐两条写路径的解。

## 2026-07-30 — Cost*/EventLogMeta 加缓存两桶字段

`CostModelBreakdown` / `CostDailyEntry` / `CostRecord` / `EventLogMeta` 加
`cache_read_tokens` / `cache_creation_tokens`,`CostSummary` 加对应 total_*。
语义与账本列一致:`input_tokens` 只是未缓存满价桶,三桶互斥、各自计价
(1x / 0.1x / 1.25x)。缓存热的 agent 输入侧 >99% 在缓存桶里,之前只传
input+output 导致 popover 把 helper 显示得比主 loop 还大。默认 0,老行为
auto_migrate 回填过不存在 NULL。生产者 [[agents/cost.py]] /
[[agents/chat_history.py]],前端镜像类型 types/api.ts。

## 2026-07-23 — Create/UpdateAgentRequest 加写侧长度上限

`CreateAgentRequest` / `UpdateAgentRequest` 的 `agent_name` / `agent_description`
从裸 `Optional[str]` 改成 `Field(None, max_length=AGENT_TEXT_MAX_LENGTH)`
(常量来自 entity_schema)。过去写请求这层不卡长度,超 255 只在读回(Agent 模型)
时才炸;现在写边界直接 422。配合 importer 侧的修剪一起补齐 #71 的写侧缺口。

## 2026-07-23 — EventLogMeta

New `EventLogMeta` (run-level header for the activity card: trigger,
input_text, final_output, lifecycle, models, cost/token aggregates) +
`EventLogResponse.meta`. `total_cost_usd` is None (not 0) when no cost
rows exist. Producer: [[agents/chat_history.py]]; consumer mirror type
in frontend types/api.ts.

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-10 — ClearHistoryResponse expanded for the scoped wipe

`ClearHistoryResponse` gained `scopes` + per-target counts (event_stream,
chat_memory, chat_instances, agent_messages, memory_rows, artifacts), disk
booleans and `disk_errors[]` — a projection of `WipeResult` from
[[wipe_service.py]]. Kept `success` True once the DB commits even if disk
deletes partially fail; `disk_errors` surfaces those.

## 2026-06-11 — identity fields dropped from request models

CreateAgentRequest.created_by, UpdateTimezoneRequest.user_id, UpdateOnboardingRequest.user_id removed — identity comes from auth_middleware exclusively (see routes/auth.py.md identity hardening entry).

## 2026-06-11 — RegisterRequest/RegisterResponse deleted; Login models slimmed

Register models gone with the endpoint. LoginRequest lost `password`, LoginResponse lost `token`/`role` — those fields only ever served the cloud password branch; local login never set them. Cloud login speaks NetmindLoginRequest/Response exclusively.

## 2026-06-11 — NetmindLoginRequest / NetmindLoginResponse

Request carries `netmind_token` (+ optional `source` entry-channel tag, e.g. "arena", consumed by Phase 2 provisioning). Response mirrors RegisterResponse's quota-seeding fields (has_system_quota / initial_*_tokens) so the frontend welcome toast survives the register->netmind-login switch, and adds display_name/email because user_id is now an opaque 32-hex userSystemCode unfit for display.

## 2026-05-21 — Onboarding schemas

Added `OnboardingProgress` / `OnboardingResponse` / `UpdateOnboardingRequest`
for the new-user onboarding checklist (see `backend/routes/auth.py.md`).
`OnboardingProgress` carries three write-once-true flags
(`first_agent_created`, `template_applied`, `dismissed`); it is stored
inside `users.metadata`, not as its own table. Also re-exported from
`schema/__init__.py` (both the import block and `__all__`).

## 2026-05-19 — AgentInfo gains last_assistant_preview / last_assistant_at

Two optional string fields added to `AgentInfo` so the frontend NM messenger sidebar can render "what did this agent last say" on rows the user hasn't opened in the current session — without first fetching that agent's chat history. The values are derived server-side in `routes/auth.py::get_agents` (one window-function SELECT over `events.final_output`) and are `None` for agents with no completed reply yet.

## 2026-05-15 — invite request DTOs removed

The short-lived `InviteRequestRequest` / `InviteRequestResponse` (added
2026-05-14 for the public `POST /api/invite/request` endpoint) are deleted.
After the architecture pivot — the public invite-request surface moved
to `narranexus-website` and NarraNexus exposes only the server-to-server
`POST /api/invite/internal/issue` — those DTOs no longer have a caller.
The new internal endpoint uses inline Pydantic models defined in
`backend/routes/invite.py` (private, single-caller).

## 2026-05-14 — FileInfo becomes a recursive tree node

`FileInfo` was flat (`filename`, `size`, `modified_at`). It now models a node
in the workspace **directory tree**: `name`, `path` (workspace-relative),
`is_dir`, `size`, `modified_at`, `children: Optional[List[FileInfo]]`.
Directories carry a `children` list (possibly empty); regular files carry
`children=None`. `FileListResponse.files` renamed to `tree`. Dotfolder
filtering is server-side — `FileInfo` never represents a hidden node.
`FileInfo.model_rebuild()` resolves the self-referential type hint. Pure
shape change (no backward compat); frontend is updated in the same change.

`FileDeleteResponse.filename` renamed to `path` because deletes accept nested
relative paths now.

# api_schema.py

## Why it exists

This file is the single source of truth for all HTTP request and response shapes exposed by `backend/routes/`. Rather than scattering inline `BaseModel` definitions across route files, api_schema.py centralizes them so that the frontend TypeScript types can be generated or manually aligned against one file. Every model here is a DTO (data transfer object) — it has no database storage of its own and no business logic.

## Upstream / Downstream

The route handlers in `backend/routes/` (agents, users, chat, jobs, mcp, files, costs) import only from this file for their request validation and response construction. The models in this file know nothing about the internal domain models (`Narrative`, `ModuleInstance`, `Event`) — that translation happens inside the route handlers themselves. The frontend `src/types/` TypeScript interfaces are the consumers on the other side of the wire.

## Design decisions

**Why not generate TypeScript types automatically from these Pydantic models?** The project is fast-moving; schema generation tooling adds a build step that slows iteration. The current contract is maintained by convention — keep the Pydantic models in sync with the TypeScript interfaces manually.

**`NarrativeInfo` and `InstanceInfo` duplicated from internal domain models**: these are presentation-layer projections, not the same objects as `Narrative` from `narrative/models.py`. They contain only the fields the frontend needs and in string-friendly formats (datetimes serialized as strings). Unifying them with the domain models was considered but rejected because the domain models carry internal state (embeddings, raw JSON fields) that should never leave the server.

**`SimpleChatHistoryResponse` vs `ChatHistoryResponse`**: the "simple" variant was added later to give the frontend a flat chronological message list without grouping by Narrative. The structured variant (`ChatHistoryResponse`) is used by the chat history panel that shows Narrative-grouped context. Both exist because the two UI panels have genuinely different data needs.

**`CostSummary` / `CostRecord`**: these are read-only analytics types with no corresponding write endpoint. They are produced entirely by aggregation queries in the cost route handler.

## Gotchas

**`DeleteAgentResponse.deleted_counts`** is a dict mapping table name to count. The keys are not stable strings declared anywhere — they are whatever the route handler decides to include. If you are writing a frontend assertion against specific keys, check the route implementation, not this schema.

**`SimpleChatMessage.working_source`** can be `"chat"`, `"job"`, `"matrix"`, or any other `WorkingSource` string value. It is stored as a raw string here (not the `WorkingSource` enum) because this DTO is agnostic to the internal enum definition.

**`RAGFileInfo.upload_status`** values (`"pending"`, `"uploading"`, `"completed"`, `"failed"`) are not defined as an enum here; they are just strings. The Gemini RAG module drives these states internally.

## New-joiner traps

- `AgentInfo.bootstrap_active` is a runtime flag, not a stored field. It is computed at request time by checking whether the agent's awareness module has a bootstrap mode active. Do not look for it in the database.
- `MCPInfo` here and `MCPUrl` in `entity_schema.py` represent the same underlying database record. `MCPUrl` is the domain entity; `MCPInfo` is the API projection with some fields stringified and some omitted.
- `EventLogResponse` is loaded on-demand (lazy loading) — the chat history endpoint returns `event_id` in each `SimpleChatMessage` so the frontend can fetch the full tool call trace separately, avoiding large payloads on the initial load.

## 2026-08-18 — `ClearHistoryResponse` 补上五个一直没上报的计数器

`WipeResult`（dataclass）→ `ClearHistoryResponse`（pydantic）→ 路由手写的 kwargs：同一个字段表
存在**三处**，且已经漂过两次。新增 `inbox_threads_count` / `inbox_thread_messages_count`
（inbox 搬到自己的表时加的），以及三个更早就漏了的 `bus_failures_count` /
`report_memory_count` / `instance_links_count` —— 后三个是新加的覆盖测试发现的，不是有人注意到。

**为什么这不是"数字不好看"的问题**：这次改造修掉的缺陷是「清空会话报告成功却什么都没清」，
而**报告**那一半原本仍然瞎 —— `/clear` 返回的 inbox 计数恒为 0，所以将来某次回归让 inbox 删除
静默失效时，响应体与一次成功的清理**逐字节相同**。那正是原缺陷当初能活下来的机制：下一张
「我清空了但 Lark 历史还在」的工单，拿到的响应体分不出这两种情况。

`test_wipe_result_fields_reach_the_api` 现在断言每个 `*_count` 都出现在本模型里**并且**被路由
真的填上 —— 有位置放却没人填会静默默认 0，与根本没有那个字段一样瞎。

## 2026-08-19 — guide_agent_provisioning 回显字段

`NetmindLoginResponse` / `CreateUserResponse` 增 `guide_agent_provisioning:
bool = False`：登录/建号响应回显服务端 onboarding 引导 Agent 供给开关
（`NARRANEXUS_ONBOARDING_GUIDE_AGENT`）。前端的"你的第一个 Agent 已就位"
coachmark 以它为门——没有这个回显，拉下服务端 kill-switch 后 UI 仍会向
100% 新注册承诺一个永远不出现的 Agent（唯一的关停手段变成发前端版本）。
默认 False 保证老后端/测试构造不受影响。

## 2026-08-27 — landing_completed

`OnboardingProgress` gained `landing_completed` (and `UpdateOnboardingRequest`
the matching optional): the one-time first-run flow (frontend `WelcomePage`) has
been seen — finished OR skipped, both count. It lives with the other write-once
flags inside `users.metadata`, so no column changed. Server-side rather than
localStorage on purpose: a user logging in from another browser or machine must
not be walked through a newcomer flow again.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
