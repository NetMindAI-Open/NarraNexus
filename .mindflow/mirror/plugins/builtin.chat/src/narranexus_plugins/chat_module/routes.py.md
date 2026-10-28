---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/routes.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — `event-log/{event_id}` 加 owner 门

这条路由连 `request` 都没收，按 (event_id, agent_id) 直接返回整轮 thinking 与全部工具
调用输入输出。兄弟路由 `/events/{event_id}` 一直是 owner-only。现在先
`require_agent_owner(request, agent_id)`（SDK seam）。

## 2026-09-08（dev 实测）— 历史行带 `bootstrap` 标记

种进历史的首程问候行在 wire 上带 `bootstrap: true`，前端据此按身份去掉自己折进会话的那份（两边文本不同：前端本地化过）。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-30 — timeline 透传 monologue 档位；顺手抽成纯函数

**这是独白提级（A′）唯一的后端改动**，理由是刷新一致性：档位如果只活在直播
路径，同一轮刷新后掉回普通 thinking，打破 [[segmentTurn]] 立的那条不变量
（「直播看到的和刷新后看到的必然一致」）。

数据一直都在，只是**读侧没读**：[[execution_state]] 的 `record_thinking` 早就
把 `monologue` 写进 `events.event_log` 的对应 step（注释原话：“the monologue
SEGMENT in its chronological slot”），本文件此前只取 `content`。所以这次是
**加一个可选字段透传，没有迁移、没有回填**（铁律 #2/#6）。

投影逻辑连同两处判断（「子集 == 并集」、换档强制 flush）**已搬出本文件**，
见 [[chat_history_timeline]]——本文件搬前 1141 行、搬后 1062 行，都远超 800
行约定，而那一块是纯
函数零 DB，是最容易搬的一块。本文件只剩 `get_event_log_detail` 里一行
`timeline = build_event_timeline(entries_content, call_names)`。

搬动时**逻辑逐行照搬**、只加档位（用脚本剥掉注释后对比确认：**零逻辑行被
删除**，新增的只有 7 行档位相关）。

## 2026-08-21 (review) — 前缀补 usr_、扇出加上限、include 用 Literal

- **C2 前缀补 `usr_`**:`_PEER_SCOPE_PREFIXES` 原为 `("agent_", TEAM_ROOM_OWNER_PREFIX)`,漏了人在
  team 房间发言这条**最主流**路径——`teams.py` 以 `from_agent="usr_<uid>"` 发,turn 落在 `usr_<uid>`
  实例里,被前缀过滤整条滤掉(相对上一轮是功能回退)。补 `USER_SENDER_PREFIX`(=`usr_`)。`usr_` 是 bus
  **sender** 前缀、不是真实 user_id 前缀(真实 user_id 裸值),故加它不会把别人私聊拉回来;行级
  `working_source` 闸保留作 fail-closed 第二道(覆盖 local 模式 X-User-Id 恰以 `usr_` 开头的边角)。
- **I3 扇出上限**:peer 实例集按 `_MAX_PEER_ACTIVITY_INSTANCES=200`(get_by_agent 是 created_at DESC,取切片)
  截断并 `logger.info`,不静默丢。上限远大于任何现实翻页深度,正常 owner 的 `total_count` 不受影响。长期
  正解是把活动流改读 events 索引,而非逐实例扫 memory JSON。
- **M1 include 用 Literal**:`include: Literal["chat","activity","all"]` 交给 FastAPI 校验(非法值 422、大小写敏感),
  删掉手写 normalize;`want_activity = include != "chat"`。
- **N1 owner-notify 行按 scope 分流(行为改动)**:同一条带 `owner_notify_content` 的 assistant 行,
  `message_type` 现在是 **scope 的函数**——
  - **owner scope**:保持 `message_type="chat"`,原文进**对话** tab(agent 自己的 IM turn 主动汇报 owner,
    长期既有行为,不变)。
  - **peer scope(A2A/team)**:强制改标 `message_type="activity"`(`content` 仍是**原文、不折叠成
    `Background activity (...)`**),否则这行两个 tab 都进不去——对话 tab 从不读 peer 实例,活动流又只留
    `message_type=='activity'`,`"chat"` 型行两边皆无。改标后进 **Activity Log**。
  原文放行**不等于**泄露对端正文:bus 侧 handler 的 `owner_visible_reply_tool_names=("send_message_to_user_directly",)`
  (`message_bus/__init__.py`)保证 `owner_notify_content` 只可能来自 owner-notify 工具、不可能是给 peer 的回复,
  所以「原文给 owner」是语义正确而非隐私回退。改动用 `peer_scoped` 收口,勿写成「所有 owner_notify 都走 activity」
  (会误导下一个人删掉 `peer_scoped` 判据 → N1 回退)。用例:`test_owner_notify_on_peer_turn_reaches_activity_verbatim`
  (activity 见原文 + chat 不见 + 该行自身 peer 正文仍不外泄)。

## 2026-08-21 — Activity Log 纳入 A2A/team 活动:owner-only + 前缀收口 + 两条流各自分页

`get_simple_chat_history` 同时喂前端的「对话」tab 与「Activity Log」tab(`ChatPanel` 按
`message_type === 'activity'` 客户端分流)。A2A 与 team 的 turn 经 `MessageBusTrigger` 以
`user_id = sender_agent_id`(对端 agent id / `TEAM_ROOM_OWNER_PREFIX+team_id`,**非 owner**)
跑 runtime,落在 peer-scoped 的 ChatModule 实例里;原来只查 `get_by_agent_and_user(agent_id, owner)`,
这些实例永远匹配不上 owner,Activity Log 一条 A2A/team 活动都看不到。

三层设计:

1. **owner-only 拉取。** 仅当 `want_activity` 且 caller 是 owner(`resolve_owner`==caller,
   `None`/`""` 皆 fail-closed 退回旧行为)才用 `get_by_agent(agent_id, module_class="ChatModule")`
   补 peer 实例;`resolve_owner` 也只在需要活动流时才查,`include=chat` 的热轮询不多付一次。
2. **数据面前缀收口(不是逐行丢)。** peer 实例集只取 `user_id.startswith(_PEER_SCOPE_PREFIXES)`
   =`("agent_", TEAM_ROOM_OWNER_PREFIX)`——只有 A2A/team scope,**其他真实用户与该 agent 的私聊
   连 memory 都不会被读**(此前"其余全拿、逐行 continue"会 2N+1 次查询读别人私聊)。循环里
   `if peer_scoped and working_source not in _A2A_TEAM_SOURCES: continue` 保留为第二道 fail-closed 闸。
3. **两条流各自分页(C1)。** 对话行与活动行原来挤在同一个 `all_messages`、共用一个 `limit` 尾切,
   前端拿到 20 条后才拆两个 tab。peer/team 是**无上限**的流,灌进来会把 owner 的「对话」tab 顶空、
   轮询还把已渲染的聊天行换掉。改为端点新增 `include=chat|activity|all`:按 `message_type` 是否
   `activity` 先分流、各自 `limit`/`offset`/`total_count`。`ChatPanel` 的对话 tab 请求 `chat`、
   inner tab 请求 `activity`;`useAutoRefresh.tickBgMessages` 只订阅 `chat`,peer 活动不再误触发
   "有新回复"toast/滚动。`all` 保留旧语义给其他调用方。

`_A2A_TEAM_SOURCES` 引用 `hook_schema.BUS_PRODUCED_SOURCES`(与 `chat_module` 同一来源,新增 bus
传输一处改全生效);`_USER_FACING_SOURCES` 一并提到模块级。对端正文从不逐字外泄(普通 peer 行折叠成
`Background activity (...)`;`owner_notify_content` 的按 scope 放行见上「2026-08-21 (review)」的 **N1** 条目)。
夹具:`tests/backend/test_activity_log_a2a_visibility.py`
(owner 见 message_bus+a2a 折叠行、`include=all` 钉住前缀+行级两道隔离、非 owner 零泄漏、活动洪流下
`include=chat` 对话不被饿死)。

## 2026-08-19 — tool_output 配名走 tool_call_id;"unknown" 两个源头都治

- **配对按 id**:并行调用下所有 call 先于任何 output 落盘、output 按完成序
  返回,「最近前驱」会自信地贴错名字(与 response_processor 的重建同规则
  同理由)。timeline 与分组 tool_calls 两个视图都 id 优先;无 id 的存量行
  回退位置法(timeline=最近前驱;分组=紧邻 output,带 id 的 output 只有在
  call 自己无 id 时才允许被位置法吃掉)。**两个视图共用一份索引**:
  `outputs_by_id`/`call_names` 在两视图之前一次遍历建好,unwrap 只做一次,
  位置兜底条件收敛为 `_pairs_positionally` helper(指针推进留在调用方——
  分组消费该行,timeline 从不消费)。此前两视图各维护一套、逐轮各打补丁,
  漂移=接口自相矛盾(timeline 说 read_file、工具卡说 web_search)。
  `call_names` 记录**已知为空**的名字并用成员判定取值(不是 or 链)——
  空名调用的 output 保持空;**带未见 id 的 output 给诚实空白**而不是
  兄弟的名字(`last_tool_name` 只服务无 id 存量行)。专项夹具:空名
  (双视图断言)、幽灵 id、并行交叉。
- **占位符零发明**:读侧(本文件)与写侧
  (response_processor.state_update 持久化空串而非 "unknown")都不再制造
  它;前端 realName() 归一化只是**历史数据**兜底,不是长期契约。
测试:test_event_log_meta.py 的 inherits_call_name(无名承接、零 unknown)
与 parallel_outputs_pair_by_call_id(交叉输出各得其名,两视图一致)。

## 2026-08-10 (PR-10) — 新增 seam 孪生端点 POST /{agent_id}/chat-history/by-instance

get_chat_history 工具的 byte-parity Http 孪生：owner-gated，调共享 [[_chat_reads]] `fetch_chat_history`（与 DirectStore 同源），返工具原 dict。区别于本文件的 GET /{agent_id}/chat-history（前端 narratives+events 视图，另一形状）。

## 2026-08-05 — `_drop_phantom_event_twins()`：旧副本行的读侧兜底

新增模块级纯函数，`get_chat_history` 在排序之后、`event_limit` 截断**之前**
调用（先滤后截，`event_limit` 才买到真实轮次）。

背景：2026-08-05 之前 [[step_4_persist_results]] §4.4 会把一轮对话复制成新的
`events` 行、每条"辅助 Narrative"一份。产生路径已删，但**已落库的行按铁律 #6
不做删除迁移**，所以在这里读侧过滤。

三条同时成立才丢，这正是副本的签名、且只有副本满足：

1. `event_log` 为空（副本从来没有过 log——复制发生在 run 收尾、源是内存 Event，
   而 §4.3 只回填 `final_output`）；
2. `final_output` 非空（有内容可复制）；
3. 集合里存在另一行，**输入相同 + `final_output` 相同 + `event_log` 非空**
   ——那就是正本。

因此：崩在第一步之前的真实轮次（无 log、也没有带 log 的孪生行）留下；同一个问题
再问一次也留下（重问有自己的 log、自己的回复）。**刻意不拿
`started_at IS NULL` 当判据**——只经过 `EventCRUD.create()` 的行天生如此，
生命周期列由 [[run_recorder]] 只写主 run 那一行（另外 Phase C 之前的历史行也
全是 NULL）。

用本地 2026-08-03 真实数据核对过：31 行 → 丢 12 行，每一行都命中
`completed + started_at NULL + 0 tool + event_log '[]'`，留下的 19 行里 0 行仍
带该签名。真机拉 `/chat-history?event_limit=0`：两个 agent 共 215 条，12 个已知
副本 id 一个都不在返回里，且剩下的重复 `final_output` 全是各自带 log 的独立轮次
（前 40 字相同而已）。测试：`tests/backend/test_chat_history_phantom_twins.py`。

**刻意留下的残余（已量化，不是漏网）**：如果**正本自己的 `event_log` 也是空
的**，副本就没有"带 log 的孪生行"可比，条件 3 不成立 → 留下。本地有 4 行属于
这一类（如 `evt_188705c45f7349ab`），它们的 `final_output` **全为空**，因此不
产生重复文字、也不产生独白气泡——用户真正报告的两件事都不受影响。
`started_at IS NOT NULL` 能把它们分开（只有正本会被 [[run_recorder]] 写这一
列），但那样也会丢掉"重问一次、崩了、回复恰好与前一轮相同"的真实轮次；**藏掉
真实轮次比多显示一个空行更糟**，故否决。另有反面样本证明条件 3 是必要的：
`evt_1e33fa7004d54298` 是**真轮次**（1 次工具调用、有真回复）却也存了空
`event_log`，它被正确保留。

## 2026-07-30 — meta carries the prompt-cache buckets

`_build_event_meta`'s cost_records aggregation now also sums
`cache_read_input_tokens` / `cache_creation_input_tokens` into
`EventLogMeta.cache_read_tokens` / `.cache_creation_tokens`. Same defect
class as the /costs popover fixed the same day: `input_tokens` is only the
full-rate bucket, so a cache-warm run's token chip showed "33 in" while
the model actually read ~869k. Buckets stay separate in the API; the
frontend sums for display ([[InnerThoughtCard.tsx]]).

## 2026-07-23 — event-log meta (activity card header)

`get_event_log_detail` returns `meta: EventLogMeta` assembled by
`_build_event_meta`: env_context.input (capped 4000 chars), lifecycle
(state/started/finished/derived duration — Phase C columns, None on
legacy rows), and a cost_records aggregation by event_id (distinct
models, token sums, total USD — None when no rows so the UI hides the
chip instead of lying with $0). Consumed by [[InnerThoughtCard.tsx]].
Tests: `tests/backend/test_event_log_meta.py`.

## 2026-07-10 — clear-history rebuilt as a scoped wipe delegating to wipe_service

`DELETE /{agent_id}/history` now takes `?conversations=&memory=` (default both
true; 400 if neither) and delegates to [[wipe_service.py]]
`wipe_agent_data(...)`. The old inline handler was incomplete — it deleted a
few DB tables but **never the on-disk narrative markdown / trajectories** (the
real memory; the DB is rebuilt from them on restart) and its session cleanup
globbed the wrong path (`{agent}_*.md` under the repo dir — the real files are
`~/.narranexus/sessions/{agent}_{user}.json`), so it deleted nothing. That glob
block is gone. Ownership is now enforced (agents.created_by, 404 for
non-owner) because `memory_*` is deleted by agent_id — a non-owner wipe would
destroy the owner's memory. `?user_id=` is rejected (TDR-12). The
"New-joiner traps" note below is superseded: the full table+disk set now lives
in `wipe_service`, not this route.

# agents/chat_history.py — 聊天历史与对话记录路由

## 为什么存在

这个文件暴露前端展示对话历史所需的所有读取接口：完整的 Narrative+Event 树（用于调试和归档视图）、简化的时序消息流（用于聊天界面）、单条 event 的 thinking/tool calls 详情（用于懒加载推理过程）。此外还提供清空历史的接口。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合；前端聊天面板、历史记录页面、调试视图
- **依赖谁**：
  - `InstanceRepository` — 查询 ChatModule 实例
  - `narranexus.platform.utils.db.db_factory.get_db_client` — 直接查询 `narratives`、`events`、`instance_narrative_links`、`instance_json_format_memory_chat`、`module_instances`、`cost_records` 表（**不含** `agent_messages`——那是墓碑表，见 [[agent_message_repository]] 2026-08-05）

## 设计决策

**两套查询路径的双重 fallback**

`get_chat_history` 有两条查询路径。主路径通过 ChatModule 实例的 `instance_narrative_links` 关联找 Narrative，更准确；fallback 路径通过 `narrative_info.actors` 字段过滤，是老版本的查询方式。如果主路径找不到任何 Narrative（比如老数据），自动降级到 fallback。这个设计是为了向后兼容历史数据，因为早期版本没有 instance-narrative 关联表。

**`simple-chat-history` 绕过 Narrative 层**

简化聊天记录接口 (`/simple-chat-history`) 不走 Narrative，直接从 `instance_json_format_memory_chat` 表读取 ChatModule 的 message 数组。这比 Narrative/Event 路径更高效，也更贴近"展示最近 N 条消息"的使用场景。分页用"从最新往旧"的方向切片，而不是传统的 offset/limit，因为聊天界面通常先显示最新消息。

**清空历史的多表级联**

`clear_conversation_history` 不只删 `narratives` 和 `events`，还清理
`instance_json_format_memory_chat` 和 sessions 目录下的文件。这是因为聊天历史
实际上分散在多个存储里，只清一个的话前端会看到数据不一致。（`agent_messages`
也在删除清单里，但那是墓碑表、恒为 0 行——不是聊天正文所在处，见
[[agent_message_repository]] 2026-08-05。上面 2026-07-10 段说明完整清单现在
在 [[wipe_service]] 里，不在本路由。）

**event log 的 thinking 重组**

Event 的 `event_log` 字段里存的是流式 delta，每个 thinking_delta 是一条独立记录。`get_event_log_detail` 需要把这些 delta 拼接成连贯的 thinking 块，遇到 tool_call 等中断时开启新块。这是懒加载推理详情时在服务端做的重组。

## Gotcha / 边界情况

- **non-chat working_source 的消息过滤**：`simple-chat-history` 对 `working_source != "chat"` 的消息只保留 assistant 角色的消息，过滤掉 user 消息。这是因为 job/matrix 触发的 user 消息是系统生成的触发提示，不应该展示给用户。如果将来有新的 working_source 类型，需要检查这个过滤逻辑。
- **分页方向**：`simple-chat-history` 的 `offset` 参数是"从末尾跳过 N 条"，而不是传统的"从开头跳过 N 条"。`offset=20, limit=20` 取的是倒数 21-40 条，而不是正向的第 21-40 条。
- **timestamp 解析的多格式兼容**：`_parse_timestamp` 需要处理 MySQL datetime（带或不带时区）和 SQLite 文本格式，代码里有一个多格式 fallback 列表。这说明历史数据里存在时间戳格式不一致的情况。

## 新人易踩的坑

删除聊天历史时，不能只删 `narratives` 和 `events` 表。聊天正文在
`instance_json_format_memory_chat`（ChatModule 的 per-instance JSON 记忆），
不清理会导致下次启动时 Agent 仍然能"记住"被删除的对话。完整清单在
[[wipe_service]]。

**别把 `agent_messages` 当聊天正文**：它没有写入者、恒为 0 行。0802
【对话时序错乱】的分析就是查了这张表发现是空的、于是推断聊天面板在回放
`events`——两个结论都错。`/simple-chat-history`（聊天面板）读的是
`instance_json_format_memory_chat`；`/chat-history`（Narrative / Runtime 面板）
读的才是 `events`。见 [[agent_message_repository]] 2026-08-05。

## 2026-08-18 — `/clear` 的响应补上五个一直没上报的计数器

`WipeResult`（dataclass）→ `ClearHistoryResponse`（pydantic）→ 本路由手写的 kwargs：同一个字段表
存在**三处**，且已经漂过两次。补上 `inbox_threads_count` / `inbox_thread_messages_count`
（inbox 搬到自己的表时加的），以及三个更早就漏了的 `bus_failures_count` /
`report_memory_count` / `instance_links_count`。

**为什么这不是「数字不好看」的问题。** 同一批修掉的缺陷是「清空 agent 会话报告成功却什么都没
清」（IM 记录搬表后，[[wipe_service.py]] 的清理循环再也找不到任何该删的行）。而**报告**这一半
原本仍然瞎：本路由返回的 inbox 计数恒为 0，所以将来某次回归让 inbox 删除静默失效时，响应体与
一次成功的清理**逐字节相同** —— 那正是原缺陷当初能活下来的机制。下一张「我清空了但 Lark 历史
还在」的工单，拿到的响应体分不出这两种情况。

`test_wipe_result_fields_reach_the_api` 现在断言每个 `*_count` 都出现在响应模型里**并且**被本
路由真的填上 —— 有位置放却没人填会静默默认 0，与根本没有那个字段一样瞎。它当场就抓出了上面
那两处更早的遗漏，不是有人注意到的。

**加字段时的陷阱**：`WipeResult.narrative_ids` 在响应里叫 `narrative_ids_deleted`，所以不能用
`**asdict(result)` 一把展开；覆盖测试因此只断言 `*_count` 这一类，而不是全字段相等。

## 2026-09-04 · data-access providers (batch 3c.4)

`ROUTES` — this router is the `backend.routes` contribution of builtin.chat (prefix `/api/agents`, mounted by `backend/plugins_host`), no longer included by `routes/agents/core.py`; disabling the plugin makes these paths 404 together with the MCP-side provider.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.chat_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
