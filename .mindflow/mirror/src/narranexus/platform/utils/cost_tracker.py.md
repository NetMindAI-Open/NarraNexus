---
code_file: src/narranexus/platform/utils/cost_tracker.py
last_verified: 2026-08-31
stub: false
---

# cost_tracker.py

## 2026-08-28 — 账目认领轮次：`cost_event_scope`

`event_id` 从来只有 step_4（`call_type="agent_loop"`）传得出来，**其余每一个
helper 调用点都硬写 `event_id=None`** —— narrative 选择、shutter/decider、
总结、post-turn hooks 全部记成"不属于任何一轮"。后果不是少一列元数据：
`chat_history._build_event_meta` 按 `WHERE event_id = ?` 汇总单轮 token，于是
它一直只数到主循环那一条，**helper 用得越多，单轮数字漏得越离谱**，而界面上
看不出任何异常。

修法沿用本文件已有的心智：既然 `(agent_id, db)` 能作为环境量漂到每个调用点，
轮次也能。新增 `_cost_event_id` ContextVar + `cost_event_scope()`，
`record_cost` 在 `event_id is None` 时回落到它。

**为什么是独立的 ContextVar，而不是塞进 `_cost_context` 那个 tuple**：每个
helper 调用点都写着 `_agent_id, _db = ctx`，加宽 tuple 要改遍所有解包点却换不
来任何东西；何况两者生命周期本就不同——agent+db 覆盖一整个 worker pass，轮次
只覆盖其中一轮。

**`set_cost_context` 故意不动 event**：轮次中途重设 cost context 时若顺手清掉
event，这些 token 就正好掉出它们所属的那一轮。两个变量生命周期不同是刻意的。

**后台 Steps 5-6 天然正确**：它们由 `spawn` 在 scope 内创建，`create_task` 在
创建时刻复制上下文，所以任务活得比 scope 长也仍带着这一轮的 id。

上游：[[agent_runtime]] 在 Step 0 之后 `enter_context`。
Tests：`tests/utils/test_cost_tracker_event_attribution.py`。

## 2026-08-31 — `cost_context_scope`：修掉中途 `clear` 抹掉本轮上下文

> 修正 08-28 条目里一句错的不变量：它写着「清理只发生在
> `clear_cost_context`（finally 兜底）和 scope 退出时」。**清理也发生在轮次
> 中间**，而且抹掉的是本轮自己的上下文。

`step_3._generate_fallback_reply_stream` 此前是 `set_cost_context(...)` +
`finally: clear_cost_context()`。它是**异步生成器**——异步生成器**不复制
context**（PEP 568 从未落地），所以那个 `finally` 清的不是私有副本，是**这一
轮 task 自己的** `_cost_context`。

后果（**本 PR 之前就存在的老 bug**，08-28 那次只是把 event 也拖了进去）：走
fallback 恢复路径的轮次（`no_reply` / `after_error`），从那个 `finally` 之后
到本轮结束，`_resolve_cost_context` 拿不到 `(agent_id, db)` 就直接 return；
Steps 5-6 在 step_4 之后 `spawn`，复制到的正是这份被清空的 context，
**post-turn hooks 的花销一行都不记**。也就是说账目恰好在**已经失败的那些
轮次**上不准，而屏幕上看不出任何异常。

修法：新增 `cost_context_scope(agent_id, db)`，`finally` 里 `reset(token)`
**还原**而非 `set(None)` 清空；step_3 改用它。`clear_cost_context()` 语义收窄
为「最外层专用」——后台 worker 收尾、`AgentRuntime` 的 post-turn task 收尾。

**`_unwind` 为什么吞 `ValueError`**：异步生成器被中途放弃时，`finally` 在关闭
它的那个 context 里跑，`ContextVar.reset(token)` 会抛
`Token was created in a different Context`。本模块的铁律是 observability, not
flow control，所以跨 context 退栈降级为清空，绝不把异常抛进一个无关调用者的
栈。**能保证的**是不抛、且执行清理的那个 context 读回 None；**不能保证**
（contextvars 做不到跨 context 写）进入 scope 的那个 context 的值——它要等自
己正常退栈。

**护栏必须驱动真实调用点。** 初版只测了「`cost_context_scope` 在同步 `with`
里能正确嵌套」——那它一直都能。把 step_3 改回旧写法，923 个 runtime+utils 用例
全绿，等于没有护栏。现在
`test_fallback_reply_stream_leaves_the_turns_context_intact` 直接驱动真实的
`_generate_fallback_reply_stream`（只 patch `get_helper_sdk`），回退即红。
自己手搓一个同形的异步生成器不算数：那只证明 scope 的行为，而 bug 在**调用
点**。

`AgentRuntime.run()` 自己也改用 `cost_context_scope`（08-31）：它并非总是最外
层——Step 6 回调能驱动嵌套 `run()`，裸 `set` 会把父层的 pair 永久覆盖。今天两
层的 agent_id 与 db 单例相同所以看不出来，第一个传不同 agent_id 的调用方出现
时就会把父轮剩余的 post-turn 花销记到子 agent 头上。

Tests：`tests/utils/test_cost_tracker_event_attribution.py`。

## 2026-08-10 — exact provider-card attribution

Ledger rows now capture the selected slot card's source. Agent-loop costs use
the agent slot; helper calls use helper_llm. This makes real free-tier usage
measurable without provider-branch heuristics.

## 2026-07-30 — 手写价目表移出；成本按 cache 三档计价

内联的 `MODEL_PRICING`(两条)整个删掉，定价改由 [[model_pricing]] 解析。
删它的理由不是重构洁癖：查线上库时那两条**没有一条命中实际在跑的 model**，
于是 llm_function / llm_stream / embedding 共 2254 次调用全部记 $0；而唯一命中
过的 `gemini-2.5-flash` 还是半价（0.15/0.60 vs 实价 0.30/2.50）。

`calculate_cost` 新增 `cache_read_tokens` / `cache_creation_tokens` 两参，返回
值多一个 `cache_cost`。**这推翻了 2026-07-23 那条「金额计算完全不碰 cache 字段」
的决定**：当时的前提是「agent_loop 走 sdk_cost_usd 已含折扣，helper 路径根本不
传 cache」。第二个前提今天不成立了 —— helper SDK 开始上报三档（见
[[anthropic_helper]] / [[cli_helper]] 同日条目），若仍按单一 input 价计，一次
全缓存命中的调用会被高估约 10 倍（cache read 是 0.1x）。

**无 cache 定价的 model 回退到 input 价，不是回退到 0**：没有公布缓存档位的
model 不等于缓存免费，记 0 会让「我们开了缓存」看起来把成本砍到没有 —— 正是这次
改动要防的假胜利。

`record_cost` 签名不变（cache 两参 2026-07-23 就在了），只是现在会把它们透传给
`calculate_cost` 而不只是写库。

Tests：`tests/utils/test_model_pricing.py`。


## 2026-07-28 — 配额扣减钩子删除

`record_cost` 尾部那段「`provider_source=="system"` 就调
`QuotaService.deduct`」的钩子整个拿掉了。免费额度的钱现在由 LiteLLM 网关在
请求路径上按美元实时计量，我们这边不再维护第二本账。

本文件回到纯粹的**可观测性**职责：往 `cost_records` 写一行 token 账，仅此
而已。`user_id` / `provider_source` 两个 ContextVar 仍然读、仍然落库 ——
它们现在只用于归属和审计，不再驱动任何扣费逻辑。

## 2026-07-23 — record_cost 扩三参:cache 埋点 + num_turns(W1)

`record_cost` 新增 `cache_read_tokens`/`cache_creation_tokens`(默认 0)与
`num_turns`(默认 None),原样写入 cost_records 同名(前两者列名带 `_input_`)
新列。**带默认值是契约**:llm_function/helper SDK 等旧调用点零改动,今天只有
agent_loop(step_4)会传非默认值。金额计算完全不碰 cache 字段——Claude 的
sdk_cost_usd 已含缓存折扣,价目表模型(GPT/Gemini)的 helper 路径根本不传。
上游链:output_transfer(提取)→ response_processor(归一化两套词汇)→
ExecutionState(累加;num_turns latest-wins)→ PathExecutionResult → step_4。

## 2026-07-22 — record_cost 落库归属 + 串联扣减流水

`record_cost` 现在在函数开头（INSERT 之前）防御式读取 `current_user_id` /
`provider_source` 两个 ContextVar（[[api_config]]），把它们写进 `cost_records`
的新列，并捕获 `db.insert` 返回的自增 id 作为 `cost_record_id`。deduct hook 复用
这两个已读的值（不再在 hook 内重复读），并把 `cost_record_id / provider_source /
model / agent_id` 透传给 `svc.deduct(...)`，好让 [[quota_repository]] 写一条能
回指 cost_records 的自审计流水。归属两列对**所有**调用记录（不止 system 分支）；
「仅 system 且有 user_id 才扣减」的不变量不变。读 ContextVar 失败一律降级 None，
绝不阻断计费（observability, not flow control）。

## 2026-07-06 — warn_missing_usage is exception-safe

warn_missing_usage wraps its logger.warning in try/except: observability must
never become flow control, so even a logging-layer failure cannot break the LLM
call path that calls it.


## 2026-07-03 — second setter + de-silence helper (Phase 0 / module H)

Two additions. (1) `set_cost_context` now has a SECOND legitimate caller besides
`AgentRuntime.run()`: the memory consolidation worker
([[memory_consolidation_worker]]) sets it per background pass so consolidation
LLM calls are finally accounted — RECORD only, it never sets `current_user_id`
so the deduct hook stays dormant (background work is never billed). (2) New
`warn_missing_usage(source, model, call_type)`: helper SDKs used to skip
recording silently when a live context existed but the provider returned no
usage; they now call this to emit an auditable L2 WARNING instead of a silent
miss. Observability only, never raises. Note: there is no embedding caller in the
codebase anymore (memory = BM25 + grep, no vectors) — the historical "OpenAI
Embeddings" caller no longer exists.


LLM API cost calculation and recording — a contextvars-based ambient context that lets any LLM call record its cost without passing `agent_id` and `db` explicitly.

## Why it exists

The agent runtime calls multiple LLM APIs (Claude via the Anthropic SDK, OpenAI GPT, Gemini, OpenAI Embeddings) in a single turn. Recording the cost of each call required either threading `agent_id` and `db` through every function that eventually calls an LLM, or using a global mutable variable. `cost_tracker.py` uses Python's `contextvars.ContextVar` to store `(agent_id, db_client)` as an ambient context tied to the current async task. `AgentRuntime.run()` sets this context once at the start of a turn; all LLM callers downstream can then call `record_cost()` without extra parameters.

## Upstream / Downstream

**Set by:** `agent_runtime/` — `AgentRuntime.run()` enters `cost_context_scope(agent_id, db)` on its ExitStack at the start (2026-08-31; it used to be a bare `set` + `finally: clear`). The remaining legitimate `set_cost_context` callers are the background workers, which genuinely are the outermost frame of their pass.

**Called by:** `agent_framework/llm/api/` (Claude SDK wrapper, OpenAI wrapper, Gemini wrapper, embedding client) — each records its token usage via `record_cost()` after a successful API call.

**Reads from:** [[model_pricing]] — `price_for(model)` resolves per-TOKEN USD rates (and the prompt-cache tiers) from litellm's maintained table. `agent_loop` rows still use the SDK's own `sdk_cost_usd` directly and skip pricing entirely.

**Writes to:** the `cost_records` table via the ambient `db_client`.

## Design decisions

**`contextvars.ContextVar` for asyncio safety.** Unlike a module-level global, a `ContextVar` is scoped to the current async task and its children. Concurrent agent runs on different asyncio tasks each see their own `(agent_id, db)` pair without interfering.

**`AgentRuntime.run()` owns the lifecycle.** The cost context is set exactly once per agent turn and cleared in `finally`. This means any function called during the turn can call `get_cost_context()` and get valid data, but functions called outside a turn (e.g., admin scripts) will see `None` and should handle that gracefully.

**Pricing coverage is upstream's, not ours.** [[model_pricing]] resolves against litellm's ~2983-entry table, so "is this model priced" is no longer a question about our own dict. An id the table does not know stays **unpriced** — `price_for` returns None, the tokens are still recorded, and one warning per model names the id to add to `_LOCAL_OVERRIDES`. Claude `agent_loop` rows bypass this and use the SDK's own figure.

**`calculate_cost` is a pure function.** It takes model name and token counts and returns a USD amount. It is separate from `record_cost` so that callers can estimate cost before committing to a database write, and so that tests can verify cost calculations without a database.

## Gotchas

**`get_cost_context()` returns `None` outside of `AgentRuntime.run()`.** Any LLM call made outside the agent runtime (e.g., in a standalone script or a test that calls an LLM function directly) will get `None` from `get_cost_context()`. The recording step will be silently skipped rather than raising an error. This is intentional — cost tracking should never block the actual LLM call.

**`ContextVar` does not propagate to `asyncio.create_task()` by default in Python < 3.7.1.** In modern Python (3.7.1+), `create_task` copies the current context, so child tasks see the parent's cost context. This is the expected behavior, but be aware that manually creating a new `Context` object and running a coroutine in it will lose the cost context.

**New-contributor trap (was: maintain the dict).** There is no dict to maintain any more — that instruction pointed at `MODEL_PRICING`, deleted 2026-07-30. What remains true is the failure MODE it warned about: an id the table cannot resolve books $0. It is no longer silent, though — `price_for` warns once per model, by name. If you see that warning, the fix is a rate card entry in `_LOCAL_OVERRIDES`, not a code change.

## 2026-04-16 addition — system-default quota deduct hook

`record_cost` now ends with an optional post-hook that deducts the
system-default free-tier budget when `provider_source == "system"` and
`current_user_id` is present (both ContextVars from `api_config.py`,
set by ProviderResolver / auth_middleware respectively). The hook is
best-effort — any failure (import error, uninitialised QuotaService,
DB outage) is logged and swallowed so cost-tracking never blocks the
user response. When the quota module is absent (local mode / tests
that do not call `QuotaService.set_default`), the hook is a silent
no-op. `user` / `None` provider_source values trigger nothing — the
invariant is "only deduct on the system branch".
