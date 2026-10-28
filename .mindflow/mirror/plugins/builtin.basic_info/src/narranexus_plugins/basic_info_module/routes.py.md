---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/routes.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-10 (PR-7) — 读端点改调共享 fetch_*，成为 byte-parity seam 孪生

view_narrative(GET)/switch_narrative(POST) 从 response_model 改为返回**裸 dict**，
body 全部委托给 [[_narrative_reads]] 的 `fetch_narrative_view`/`check_narrative_switch`
——DirectStore 调**同一批**函数，故两路逐字相同（旧 response_model 形状不是工具
shape、无法 parity）。**新增 GET `/{agent_id}/events/{event_id}` → view_event**
（`fetch_event_view`，原本没有 event 的 seam 孪生）。三个读端点整体包 try 兜住
`get_db_client()` 获取失败（fetch_* 自身不抛）→ 200+`{success:False,error}`，与
DirectStore 对齐。删了 `NarrativeViewResponse`/`NarrativeSwitchResponse` 模型与本地
`_parse_info`/`_narrative_chat_history`/常量（提到 [[_narrative_reads]] 去重）。
前端只用 chat_history 的 `/event-log/`，不碰本文件的 `/narratives/`，故 reshape 安全。
create_narrative(POST) 仍用 NarrativeService 真建、保留 response_model，不动。

## 2026-08-10 (round-3 review #1) — 链接上界修正 + truncated 标记

`_narrative_chat_history` 的 chat 扇出 N+1 修复(get_by_ids 循环→单次 get_by_ids)
之后,链接侧上界有两个坑,本次修:
- **过滤在截断之后**:`instance_narrative_links` 挂 aware_/social_/… 多类实例,
  原来先 `limit=200` 再过滤 chat_ 会在前 200 行多为非 chat 时**静默丢**聊天段。
  改为 `_MAX_NARRATIVE_LINKS=500` 抓行、过滤 chat_、再 `[:_MAX_CHAT_INSTANCES=100]`;
  命中上限时返回 `truncated=True`,不再无声截断。
- **无 ORDER BY**:LIMIT 不定序取的是引擎心情。加 `order_by="created_at DESC"`
  取最近的链接。
- 三个同值 `200` 字面量(链接行/消息/扇出)抽成语义各异的 `UPPER_SNAKE` 常量。
数据访问仍只走 `db.get`/`get_by_ids`(无裸 SQL)。round-4 补回归测试
`test_chat_history_survives_when_newest_links_are_non_chat`(前 200 链接全非
chat + 之后一条 chat,断言历史不丢)与
`test_over_cap_chat_instances_flag_truncated_and_fan_out_100`(>100 chat 实例
断言 truncated=True 且只扇出 100);fake db 真实现 order_by DESC——把代码回退
成旧的「limit=200 先取后过滤」两条测试即红(已验证),非形式测试。


## 2026-08-10 (pre-open review) — user_id 只信认证身份 + 扇出上界

- create 的 user_id 不再来自 body:assert_owned 只证明 agent 归属,
  body 里的 user_id 可把行归到任意用户名下——改由
  `resolve_current_user_id` 派生(本端点的写分支是本 PR 净新增,没有
  「忠实复刻」豁免)。
- `_narrative_chat_history` 的 chat 实例扇出截断到 100(共享 API 进程
  上无界 N+1 = 所有人的慢请求;MCP 孪生在模块进程里只慢自己)。
- title ≤300 / description ≤2000 上界。

# agents/narrative.py — Narrative/event endpoints for the MCP data-access seam

## 为什么存在

BasicInfoModule 的 narrative/event MCP 读工具（`view_narrative` / `view_event` /
`switch_narrative`，见 `_basic_info_mcp_tools.py`）**已（PR-7）走 AgentDataStore
seam**：本文件的读端点就是它们的 Http 孪生，HttpStore 调这里，mcp 容器不再需要 db
凭据。读端点的 body **全部委托给 [[_narrative_reads]] 的 `fetch_narrative_view`/
`fetch_event_view`/`check_narrative_switch`**——DirectStore 调**同一批**函数，所以
两路返回逐字相同（parity=单一实现）。这些 helper 走 `AsyncDatabaseClient` 的
`get_one`/`get`/`get_by_ids`，方言安全，无裸 SQL。`create_narrative` 端点是唯一
的写，仍自己调 `NarrativeService`（见下）。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 挂到 `/api/agents`；HttpStore 调
  `GET /narratives/{id}`、`GET /events/{id}`、`POST /narratives/{id}/switch`。
  前端只用 chat_history 的 `/event-log/`，不碰本文件的 `/narratives/`。
- **依赖谁**：
  - `assert_owned` — 每个端点先做 owner 校验
  - [[_narrative_reads]] — 三个读的实际实现（方言安全 + agent 归属过滤）
  - `NarrativeService.create_narrative` — `POST /narratives` 的实际建表逻辑

## 设计决策

**`create_narrative` 端点直接创建，不是信号**

MCP 工具版本的 `create_narrative` 只是一个信号——它自己不写库，靠
`agent_runtime` 里 `step_4_persist_results._detect_narrative_routing_signal`
扫描 agent-loop 的 tool-call 输出、事后调 `NarrativeService.create_narrative`
真正建表。这个信号机制存在的原因是 MCP 工具进程和 runtime 进程是分开的，除了
"扫工具调用记录"没有别的方式通信。

这个 Http 端点没有这层进程隔离，也没有 runtime hook 可以托付，所以选择**直接
调 `NarrativeService.create_narrative` 建表**并把真实 `narrative_id` 返回给
调用方——如果也做成"只签收不建表"，调用方就永远拿不到 id，端点等于没用。

**`view_narrative` / `view_event` / `switch_narrative` 按 `agent_id` 归属过滤**

（实现在 [[_narrative_reads]]，route 与 DirectStore 共用。）旧裸 SQL 工具只按 id 查、
不核对 `agent_id`，所以任何 agent 传别人的 narrative_id/event_id 就能读到别人的
内容（跨租户读）。迁移后每个读都做 `row["agent_id"] == agent_id`；不匹配返回和
"不存在"完全相同的 `success=False` 文案，不泄露"这个 id 存在但不是你的"。

**chat 历史读经 [[_narrative_reads]] 的 `narrative_chat_history`（单一来源）**

`_narrative_chat_history` 曾同时存在于本 route 与工具（两份逻辑手工同步）。PR-7
把它提到 [[_narrative_reads]]，route（经 fetch_narrative_view）与 seam 的 DirectStore
共用同一份——不再有手工同步纪律。过滤逻辑不变：`chat_` 前缀实例、按
`meta_data.timestamp` 排序、扇出触 100 上限时置 `truncated`（不静默丢历史）。

## Gotcha / 边界情况

- **`view_narrative`/`switch_narrative` 对"narrative 不存在"和"narrative 属于
  别的 agent"返回同一个错误文案**：这是刻意的（见上面设计决策），不要在排查
  "为什么找不到 narrative"时假设两者可区分。
- **`create_narrative` 的 400-shape 是 200+success:false，不是 HTTP 400**：
  `title` 为空时不抛 `HTTPException`，走和其他失败一样的 200 响应体，和
  agents 家族其余端点（awareness.py 等）的失败 shape 保持一致。
- **`links` 里非 `chat_` 前缀的 instance（如 `aware_...`）会被过滤掉**：
  `instance_narrative_links` 表挂的不只是 ChatModule 实例，聊天记录只从
  `chat_` 前缀的 instance 拉，其余静默跳过。

## 新人易踩的坑

新增/修改这三个端点时，同步检查 `_basic_info_mcp_tools.py` 对应工具有没有变
——两边是"同一份产品语义、两个实现"，MCP 工具那边改了过滤规则或返回字段，这
里也要跟上，否则 HttpStore 和 DirectStore 路径会出现行为分叉（parity 测试的
坑，参照 `awareness.py.md` 2026-08-10 条目里 `create_missing` 的先例）。

## 相关约束

- `.mindflow/project/references/narrative_system.md` —— Narrative 选择/
  Instance-Narrative 绑定的全貌，理解 `instance_narrative_links` 语义时读
- 铁律 #21（placement rule）—— 本文件在 `backend/routes/`，只经 HTTP 被
  agent 进程消费，不被 `xyz_agent_context` 反向 import

## 2026-09-04 · data-access providers (batch 3c.4)

`ROUTES` — this router is the `backend.routes` contribution of builtin.basic_info (prefix `/api/agents`, mounted by `backend/plugins_host`), no longer included by `routes/agents/core.py`; disabling the plugin makes these paths 404 together with the MCP-side provider.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.basic_info_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
