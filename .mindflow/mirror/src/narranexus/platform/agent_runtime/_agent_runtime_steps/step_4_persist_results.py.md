---
code_file: src/narranexus/platform/agent_runtime/_agent_runtime_steps/step_4_persist_results.py
last_verified: 2026-08-26
stub: false
---

## 2026-08-25 — 冻结来源分标签(PR #361 round 2, M1)

`is_default` 合流了两个冻结来源(legacy 桶行 / 本轮判"无持久话题"),
但日志标签不再共用:真桶行在**有**持久话题的轮次上曾被记成
"no_durable_topic"——那是排查路由投诉时唯一的现场证据行。现在按
`ctx.no_durable_topic` 分为 `no_durable_topic` / `default_bucket`,
行为(is_default 分支)不变。


## 2026-08-18 (review 修正) — 两个 helper 合并成一个遍历

上一条新增的 `_owner_visible_reply_texts` 跟既有的 `_turn_delivered_user_message` 是同一段
遍历的两份拷贝。当时给的理由是"布尔契约挂在 session anchor 上，改形状代价不对称"——**这个
理由不成立**：`bool([])` 跟原来的 `return False` 在几乎所有输入上逐值等价，包括
`extract_owner_visible_text` 返回 `""` 那条。

> **2026-08-18 review 修正**：上面那句原本还写着"和异常路径"，**这是错的**，而且错在最
> 要命的一格。等价性有**一个例外**：旧版首个命中就 `return True`、永远看不到后面的元素；
> 新版走完全程，所以**命中之后**再有元素让 handler 抛异常时，会走进 `except` 返回 `[]`,
> 布尔读数从 `True` 翻成 `False`。
>
> 这是**有意选择**：读不干净的响应统一取保守的"未投递"——session anchor 不动、guard 跳过
> 这一轮，对两个消费者都是安全侧。
>
> **所以：不要**为了"保留已经收集到的部分"把 `except` 改成 `return texts`。那才会让两种
> 读法在部分损坏的响应上真正分叉，而分叉的症状是探针在一部分流量上静默失效、数字却依然
> 好看。源码 docstring 里也写了这句，但按铁律 #10 先读 mirror 的人只会看到这里——所以这条
> 必须两边都在。
>
> 由 `test_a_raise_after_a_real_reply_reads_as_not_delivered` 锁住（要走到 `except` 得替换
> 整个 handler：handler 是 frozen dataclass，而塞畸形输入会被 `isinstance` 过滤掉，那样的
> 测试等于什么都没断言）。

现在 `_owner_visible_reply_texts` 是这个文件里"owner 看到了什么"的**唯一实现**，
`_turn_delivered_user_message` 就是它的 `bool(...)`。

代价不在今天——今天两份行为一致。在于下次改 `resp.details` 形状、registry 取法或 owner-visible
判定口径时得记得改两处。改漏的后果不对称且难查：session anchor 认为投递了、temporal guard
认为没有（或反之），后者会让探针在一部分流量上静默失效，**而探针失效的唯一症状就是数字
看起来很好**。

`tests/agent_runtime/test_owner_visible_delivery.py` 补了针对列表版的断言（多条回复保序、
bus 回复不计入、空白回复不计入、畸形输入返回 `[]`），外加一条直接断言两种读法在所有 case 上
一致——把合并依赖的等价性写成测试而不是留在注释里。

## 2026-08-18 — 新增 4.8：temporal guard（纯诊断）

读本轮**已经投递给 owner** 的回复，把"今天是 X"这类对当下日期的断言跟时钟对一遍，不一致
就往 `service_audit` 记一行。实现见 [[temporal_guard.py]]。

**不改回复、不重新提示、不打断任何东西。** 铁律 #15/#16 把"平台修正模型输出"划在界外；
而且从时序上也来不及 —— 走到这里时消息早就发出去了。

放在整个 step 的**最后**，在所有持久化之后：即使它内部出现病态失败，也不会让这一轮已经
做完的真实工作付出代价。全程 try/except + fail-open。

取文本用的是 `_owner_visible_reply_texts`，跟 `_turn_delivered_user_message` 走同一个
`MessageSourceRegistry` 真值来源，所以"用户看到了什么"这个判断不会在两处分叉。没有合并成
一个函数：那个的 boolean 契约挂在 session anchor 这条路径上，改形状的代价不对称。

## 2026-08-17 — 「在用户聊天里可见」按该轮桌上的那个 owner 工具判定

owner 面的工具有两个名字，判据从写死一个改成「这个来源的桌上carry 的那个」：owner 聊天
是 `reply_owner`，其余全是 `notify_owner`。写死任一个，都会在用另一个的表面上把成功的
回复读成「没回复」。

## 2026-08-16 — 没有 narrative 的一轮不等于没有东西要存（D-10）

早退条件从 `not main_narrative or not execution_result` 收窄为
`not execution_result`，4.0/4.1/4.2/4.4 各自加 `if main_narrative is not None`。

**"没有 narrative"这件事本身是设计**：ephemeral（voice/F28）就是要不留痕迹，好让
下一条打字消息的连续性判定像这轮没发生过；④-A′ 的"无锚点 + ephemeral"也落在这里。
**不是设计的**是那条早退顺手带走的两段 event 级记账：

- **4.3** `update_event_in_db(final_output / event_log / module_instances)` +
  `MemoryEngine.index` —— events 行永远没有回复正文，`remember` 永远搜不到这轮
- **4.6** `record_cost` —— token 烧了、回复发了、**成本不入账**

这两段跟"这轮属于哪条线"没有任何关系。ephemeral 要的是不留**线**，不是不留**账**。
（核实过：配额扣减不在这条路上——`record_cost` 只写 `cost_records`，扣减在
provider driver 层随 LLM 调用发生。所以这是统计/账目缺口，不是收入漏损。）

4.5 保持原样即可：它本来就 `if main_narrative:` 才写 `current_narrative_id`，
`last_response` 无条件写——正是裸跑轮需要的（有话给下一轮比，锚点不指向不存在的线）。

`current_round` / `total_toolcalls` / `unique_narratives` 提到函数顶部给了"没有可
数的东西"的默认值，因为它们原来在 4.1/4.2/4.4 里赋值、却在末尾的完成消息里被读。

顺带把 4.4 的冻结触发条件从"这行是不是 default 桶"改成"**这一轮有没有持久话题**"
（`ctx.no_durable_topic`）——同一个分支，诚实的触发条件。
## 2026-08-12 — §4.3 回写 `event_log`，不再只回写 `final_output`

§4.3 一直只把一个字段同步回内存里的 Event：

```python
ctx.event.final_output = execution_result.final_output
```

而 `ctx.event` 是 §0 用 `event_log=[]` 造出来的（`_event_impl/crud.py`），
**从创建到运行结束都没人给它赋过值**。于是 §4.3 之后所有拿 `ctx.event` 的下游
（§4.4 的 narrative 更新、§5 的 hooks）被告知"这一轮什么都没干"—— 内存对象和刚
写进库里的那一行**互相矛盾**。

这个半同步已经咬过一次：2026-08-05 那条记录里的幽灵行，正是从这个内存对象复制
出来的，签名里就有 `event_log='[]'`。

第二次是 A1（narrative 抽取不看工具调用）。修 A1 时给 updater 加了
`build_action_digest(event.event_log)`，把这一轮的工具动作压进检索面 —— 但
`event.event_log` 是空的，**整个改动在生产上是个静默空转**，而单元测试因为自己
手工构造 Event 全都是绿的。

改动是一行，与既有的 `final_output` 回写并排：

```python
ctx.event.final_output = execution_result.final_output
ctx.event.event_log = event_log_entries
```

钉住它的测试：`tests/agent_runtime/test_step4_event_log_sync.py`。它们都是
**穿过真实 step_4** 跑的，不是手工构造 Event —— 这是唯一能让"内存对象撒谎"
这类 bug 露头的测法。内容保真（`web.log` 在、thinking 条目在）由
`test_synced_event_log_carries_the_turn_content_faithfully` 断言。
（历史注：这里原来还有一条 `..._reaches_the_action_digest`，随 digest
机器在 2026-08-26 整段删除而退役 —— digest 被测出改写 continuity 锚点，
见 updater.py.md 的 2026-08-26 条目。同步本身与它的守卫不受影响：
同步的活着的消费者是持久化行与 §5 hooks。）

**给后来者的一般教训**：把库里更新过的字段回写内存对象时，**要么全部回写，要么
一个都别回写**。挑一个回写最危险 —— 对象看起来是新鲜的，实际只有你当时关心的那
个字段是新鲜的。
## 2026-08-05 — §4.4 一轮只写一行 Event（0802「对话时序错乱」根因）

§4.4 原来对 `ctx.narrative_list[1:]`（辅助 Narrative）逐条调
`event_service.duplicate_event_for_narrative(ctx.event, narrative.id)`，
把同一轮对话**复制成新的 `events` 行**。三处叠加成了用户看到的乱序：

1. 复制发生在 run 收尾，源是**内存里的** `ctx.event`——§4.3 只把
   `final_output` 同步回内存，`event_log` / `module_instances` 从未回填。所以
   副本行是 `event_log='[]'`、`module_instances='[]'`。
2. 生命周期列由 [[run_recorder]] 只写主 run 那一行（`run_id` = 主 event id），
   副本行因此永远 `state='completed'` + `started_at IS NULL` +
   `tool_call_count=0`。
3. 副本的 `created_at` 是 run **结束**时刻，不是提问时刻。

于是从 `events` 表回放的界面（[[chat_history]] `/chat-history` → 前端
[[NarrativeList.tsx]] / [[AwarenessPanel.tsx]]）把同一轮显示最多 3 次，且排在
更新的对话**下面**——"已经回答过的老问题又冒出来"，`final_output` 那段独白也成了
独立条目。这就是 0802 现场多人复现的签名（实锤 08:21:08.370/.403 两组；本地
2026-08-03 数据同样可复现，31 行里 12 行是副本）。

**现在的语义**：一轮对话**只被写入一条线程** = `narrative_list[0]`（经 §4.0
routing 后的头部）。`narrative_list[1:]` 是 step_1 为了拓宽**读侧**上下文而按
BM25 拉进来的邻居（`MAX_NARRATIVES_IN_CONTEXT=3`），它们通过把**同一个
event id** 追加进各自的 `narratives.event_ids` 建立关联——多对多本来就该放在
这个列表列上，邻居线程仍能通过 `select_events_for_context` 回放这轮对话。
`events.narrative_id` 保持单值。`update_event_narrative_id` 移出循环、只调一次。

`duplicate_event_for_narrative` / `EventCRUD.duplicate` 一并删除（铁律 #2，不留
休眠接口）。已落库的旧副本行按铁律 #6 不做删除迁移，由 [[chat_history]] 的
`_drop_phantom_event_twins()` 在读侧兜底。

**顺带修掉同族的第二种重复：一条线程被访问两次。** §4.0 的 `switch_narrative`
把 `narrative_list[0]` 指向 agent 点名的那条线程，而那条线程**可能本来就在
列表里**（BM25 也把它当邻居捞进来了）。于是循环会撞到同一条 narrative 两次：
修复前多产出一行副本，修复后仍会往它的 `dynamic_summary` 里追加**第二条一模
一样**的条目。现在按 narrative id 去重（保序），进度消息里的
`Narratives=` / `narratives_updated` 也改报去重后的条数。真实证据：本地
`evt_f590aef867f14187` 这行副本的 `narrative_id` 与它自己的正本相同。

测试：`tests/agent_runtime/test_step4_event_attribution.py`（真 SQLite 驱动整个
step_4，断言 `events` 只有一行、无幽灵签名、三条 Narrative 都引用同一个 id）。

## 2026-08-04 (review 修正) — 锚点判定改用 owner-visible 谓词

`_turn_delivered_user_message` 由 `extract_reply_text` 改为
`extract_owner_visible_text`：bus 轮对 peer agent 的交付不再触发
proactive-delivery 分支（否则每次 A2A 回复都会把 owner 的
current_narrative_id 指到 bus narrative 并清空 last_query——PR #230
review Important #2）。函数 docstring 的契约（"surfaces in the user's
chat"）从此与实现一致。测试：
tests/agent_runtime/test_owner_visible_delivery.py。

## 2026-07-29 — 删除 4.7 句柄持久化(T5),−96 行

`_persist_cli_session_handle` 及其调用点删除。

原来那个位置约束(4.7 必须在 4.0 和 4.5 之后)是承重的:句柄锚定在
`ctx.session.current_narrative_id`,而 4.0 的中途 `switch_narrative` /
`create_narrative` 和 4.5 的主动投递都可能重新指向它,存"路由后"的叙事才能让锚点
匹配对话实际延续的那条线。

现在**没有句柄要存、也没有锚点要对齐** —— 见 [[transcript]]。这条约束消失的直接
后果:中途切叙事不再导致下一轮冷启动。

## 2026-07-28 — [4.7] 存的是 **routing 之后**的 narrative：刻意、fail-open（review FIX 2）

**零行为改动，纯文档 + 回归钉。** review 指出：step_3 用**轮前**的
`session.current_narrative_id` 校验句柄，而 4.0（agent 中途调
`switch_narrative` / `create_narrative`）会在 4.7 落库**之前**改写它，因此存进
`agent_cli_sessions.narrative_id` 的可能不是当初通过校验的那个 narrative。

结论是**接受并写清楚**，不改行为：

- 存储的 `narrative_id` 只被 step_3 校验闸门当等值锚用，不一致时 **fail-open**
  ——下一轮读到的 `session.current_narrative_id` 已经是被路由到的那个：
  会话继续留在新线程 → 两边**相等**，resume 正常（这也正是想要的：CLI 会话
  确实延续进了那个线程的这一轮）；若哪天不等 → 日志
  `COLD reason=narrative_changed` + 冷启动，代价仅**一次冷启动**，绝不会在
  未获批准的 narrative 下 resume。
- 反过来存**routing 之前**的 narrative 只会让每个被路由的轮次都必然错配，
  即严格更多冷启动、correctness 上零收益。
- 注释落在 `_persist_cli_session_handle` docstring + 4.7 调用点（后者把
  "4.7 必须排在 4.5 之后"扩成"**4.0 与 4.5 之后**"）。
- 钉子：tests/agent_runtime/test_resume_narrative_routing.py —— 用真 SQLite
  驱动整个 step_4（含 4.0），断言三件事：存的是 routed narrative；下一轮留在
  routed 线程则 RESUME；下一轮回到原 narrative 则 None +
  `COLD reason=narrative_changed`。

## 2026-07-28 — [4.7] 抽为 `_persist_cli_session_handle` + resume_failed 删旧写新（resume 化 R3）

4.7 整段抽成模块级 `_persist_cli_session_handle(ctx, execution_result)`
（可单测；调用点一行）。新语义：`execution_result.resume_failed` 为真 →
**先 `delete_handle` 删陈旧行**——即使冷启动重试没报新 cli_session_id 也删，
否则下一轮还会踩同一具尸体——再按新 cli_session_id 正常 upsert（重试产生的
是**新** session_id，删旧写新一步完成）。守卫拓宽为
`(cli_session_id or resume_failed) and ctx.session`。清句柄只在
orchestrator 侧做：适配器跑在 Executor 容器里没有 DB。fire-and-forget 契
约不变（任何失败仅 warning）。测试：
tests/agent_runtime/test_step4_cli_handle_persistence.py（真 SQLite schema）。

## 2026-07-25 — 新增 [4.7] CLI 句柄落库(resume 化 R1)

4.6 之后:`execution_result.cli_session_id` 且 `ctx.session` 存在 → upsert
`agent_cli_sessions`(经 [[cli_session_repository]])。narrative_id 取
`ctx.session.current_narrative_id`——**4.7 必须排在 4.5 之后**:proactive 分支里
4.5 会先把 session 锚点重指到 main_narrative,4.7 随后读取天然一致。指纹/
working_path 由 step_3 随 PathExecutionResult 带出,这里不重算;两者任一缺失
(step_3 fail-open)→ 跳过并 warning,不落半残行(表列 NOT NULL)。整段
try/except + warning-only,照 4.6 的 fire-and-forget 风格,永不阻断管线。

## 2026-07-23 — [4.6] record_cost 透传 cache/num_turns(W1,纯搬运)

`record_cost(...)` 调用新增 `cache_read_tokens`/`cache_creation_tokens`/`num_turns`
三个实参(来自 execution_result)。fire-and-forget 契约不变。

## 2026-06-08 — interaction index (chat+event merge)

After the event's `final_output` is set, step 4 writes ONE interaction index into `memory_event` via `MemoryEngine.index('event', event_id, user_input + final_output)` — the per-turn searchable conversation surface, with a `source_ref` back to the event. This is the chat/event merge: `remember` finds the interaction, then the agent fetches the full agent-loop trace via `view_event` (or the conversation context via `get_chat_history`). Best-effort (an index failure never breaks persistence). It replaces the retired `memory_chat` search-mirror that ChatModule used to write (see [[chat_module]]).

## 2026-05-20 (Fix #2 P3) — 4.0 narrative routing signal

New step 4.0 honors the agent's switch_narrative / create_narrative tool calls
(basic_info MCP — see [[basic_info_module.py]]). `_detect_narrative_routing_signal`
scans `execution_result.agent_loop_response` for the LAST such call; on switch it
loads the target narrative, on create it makes one via
`narrative_service.create_narrative`. The target then becomes the head of
`ctx.narrative_list` (note: `ctx.main_narrative` is a read-only property over
`narrative_list[0]`, so we override the list, not the property) + the local
`main_narrative`, so the event attribution (4.4), markdown stats (4.2), summary
updates, and the session anchor (4.5) all flow to it; `session.current_narrative_id`
is repointed so the NEXT turn continues there. It ALSO re-binds THIS turn's chat
persistence: step_5's ChatModule hook writes to the module object's
`self.instance_id` (bound in step_1 to the ORIGINAL narrative's chat instance),
so 4.0 calls `_ensure_user_chat_instance(target)` and resets every ChatModule in
`ctx.module_list` (`instance_id`/`instance_ids`) + `ctx.user_chat_instances[target]`
BEFORE step_5 runs — so the message lands in the thread it now belongs to, not
the original. (Rebind is best-effort/try-except: on failure the event is still
re-attributed and the message just stays in the original thread.)

## 2026-05-20 — 4.5 anchor on ANY user-visible delivery (not just human turns)

Supersedes the 2026-05-19 "仅在人-回复轮写 last_response" rule below. The session
continuity anchor must track the **last message visible in the user's chat
box** — which includes a message the agent DELIVERED to the user this turn even
from a background trigger (a scheduled job / heartbeat can call
`send_message_to_user_directly`; from the user's POV that's the latest
interaction). New module-level `_turn_delivered_user_message(agent_loop_response,
working_source)` reuses the `MessageSourceRegistry` reply-tool detection (no
chat_module import — modules stay hot-pluggable, 铁律 #3). Anchor condition is
now `is_user_chat OR delivered_user_message`. For a proactive (non-human)
delivery, Step 1 skipped the anchor, so step_4 sets `current_narrative_id`
= this turn's narrative, clears `last_query`/`last_query_embedding` (no prior
user query) and sets `last_query_time=now`; `last_response` = the delivered
message. Pure machine traffic (a job/bus turn that did NOT message the user)
still leaves the anchor untouched. See [[narrative_service.py]] /
[[session_service.py]] 2026-05-20.

## 2026-05-19 — 4.5 仅在「人-回复轮」写 `last_response`

Background trigger runs (`JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY`)
不再覆盖 `session.last_response`，跟 Step 1 的 `last_query` 守护对齐。
判断口径统一走 [[hook_schema.py]] 的 `WorkingSource.is_from_human()` —
CHAT / LARK / SLACK / TELEGRAM 是 True；上面 4 个是 False。`working_source`
为 None / 未知字符串时默认按 human 处理（legacy safe default）。这是
short-reply 连续性崩 bug 修复的另一半（详见 [[narrative_service.py]]
2026-05-19 段）。

# step_4_persist_results.py — Pipeline Step 4: Persist Turn Results

## Why It Exists

After the LLM turn completes (Step 3), all results must be durably written to the database before the WebSocket connection closes. This step is the "commit point" of a turn: Trajectory records, Narrative summaries, Event status updates, Session state, and cost accounting all happen here. Steps 5–6 (hooks) run as background tasks after this step completes.

## Upstream / Downstream

**Called by:** `agent_runtime.py` — Step 4 in the 7-step pipeline

**Reads from ctx:**
- `ctx.execution_result` — the `PathExecutionResult` from Step 3
- `ctx.narrative_list`, `ctx.active_instances` — for Narrative update logic
- `ctx.event` — updated with final status
- `ctx.session` — updated with last-active timestamp

**Writes to DB (6 sub-steps):**
1. **Trajectory** — full turn record (input, output, tool calls, token usage)
2. **Markdown stats** — updates Module instance Markdown with turn statistics
3. **Event update** — marks Event as completed/failed with result summary
4. **Narrative update** — updates narrative summary and typing (default/main/auxiliary)
5. **Session** — saves updated session state
6. **Cost recording** — records LLM token costs to `agent_cost_log` table

## Key Design Decisions

### Narrative Typing Logic
Each Narrative in `ctx.narrative_list` gets typed by **list position**, not by
anything the LLM decided in Step 3:
- **main**: index 0 (post-§4.0 routing) *and* not a default Narrative — the one
  thread the turn is authored into. Only this one triggers the async LLM
  summary update.
- **default**: any Narrative with `is_special == "default"`, wherever it sits —
  it only collects the event id, nothing else.
- **auxiliary**: every other entry — a BM25 neighbour step_1 pulled in to widen
  the read-side context. It gets the event id + a `dynamic_summary` entry, no
  LLM update (`updater.py` has a standing TODO for an auxiliary-specific
  prompt).

The typing is not a persisted column — it is recomputed from list position on
every turn.

### Event Final State
The Event record (created in Step 0) is updated here with: final status (`completed`/`failed`/`cancelled`), response summary, token counts, and duration. Downstream analytics and Job scheduling depend on Event records being consistently closed.

### Cost Recording Deferred to Step 4
Although token usage is tracked throughout the turn in `ExecutionState`, the final cost record is written here (not in Step 3) because it requires the final accumulated totals from `accumulate_usage()`. Writing partial costs mid-turn would create duplicates.

### Sub-step Granularity
Each of the 6 sub-steps yields a `ProgressMessage`. This gives the frontend visibility into which persistence operation is slow (e.g., a slow Narrative embedding update), which is useful for debugging production latency.

## ContextData Mutations

Step 4 does not mutate `RunContext` fields — it reads and writes to the database. However, `ctx.event.status` is updated in-memory as a side effect (to reflect the final state before saving).

## Gotchas / Edge Cases

- **Narrative update order matters**: Narrative embedding must be updated before Markdown stats, because the embedding depends on the current narrative summary which may have just been updated.
- **Failed turns still persist**: Even if Step 3 raised an exception, Step 4 runs (in a `finally` block in `agent_runtime.py`) to record the failed Event and any partial trajectory data. Do not assume `ctx.execution_result` is always fully populated.
- **Cost recording is non-fatal**: If the cost insert fails (e.g., DB constraint), the error is logged but does not raise. A missing cost record is better than a failed turn.

## Common New-Developer Mistakes

- Adding new DB writes after Step 4 in the main pipeline: anything that needs to be durable before the WebSocket closes must go here. Steps 5–6 run as background tasks after the socket closes.
- Writing a per-Narrative COPY of the turn's `events` row to associate it with more than one thread. `narratives.event_ids` is a list — that is where the many-to-many lives. See the 2026-08-05 entry above for what copying cost us.
- Forgetting to handle the case where `ctx.execution_result` is `None` (cancelled turn) — all sub-steps must guard for this.

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
