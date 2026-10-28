---
code_file: src/narranexus/platform/narrative/_narrative_impl/continuity.py
last_verified: 2026-08-27
stub: false
---

## 2026-08-26 — 锚点块与上一轮块改由共享块渲染(prompt 字节不变)

`narrative_context` 与 `previous_turn` 两段的拼装搬进 [[routing_blocks]]。
**continuity 的 user_input 一个字节没动**,包括出生证退休那条逻辑
(`description_if_unsummarised()` 返回空则整行消失)、`[Special Default
Narrative]` 标签、结尾那句关于桶边界的 Note,以及**agent 主动消息变体**
(没有上一轮用户提问时那段"the agent messaged the user proactively")。
golden 断言在 `test_merged_routing_prompt.py`。

为什么动一个被实测数字钉着的文件:合并调用需要**同样**这两段文本,而主动
消息变体正是拷贝最容易丢的那种细节 —— 它只在定时任务给用户发过消息、
用户回一句"好"的轮次出现,回放语料里几乎照不到。共享而不是复制,是让第四个
消费者不可能悄悄丢掉它。

**continuity 的判定行为一点没变**:它仍然是两次调用路径上的第一 tier,
只在 `NARRATIVE_MERGED_ROUTING_ENABLED=0` 时被调用(开时 `select()` 在它
之前就提前 return 了)。

## 2026-06-10 — helper obtained via get_helper_sdk()

`self.sdk` is now `get_helper_sdk()`. NOTE: on the anthropic helper the
per-call `model=`/`reasoning_effort=` overrides (CONTINUITY_LLM_MODEL etc.,
OpenAI-flavored names) are intentionally ignored — the slot model wins;
see AnthropicHelperSDK._resolve_model.


# continuity.py — LLM-based "does this query continue the current Narrative?"

## 2026-08-27(round 3 更正)— 0.0 兜底是防御性的,不是修活雷

last_query_time 是 pydantic 必填字段,三个构造点都会在构造期
ValidationError——"session 有文本但时间为 None"经由现有构造器不可达。
兜底保留(共享 helper 的 None 契约在此处要有明确答案),注释已改口。

## 2026-08-27 — 时间差计算收编到共享定义(round 2 I3)

内联的"距上一轮多少分钟 + naive→UTC 兜底"改调
[[anchor_rules]].minutes_since——与合并路径同一份定义,防两条决策路对
"过了多久"给出不同答案(routing_blocks 记的三次静默分叉正是这么长出来
的)。顺带收掉一个既有雷:session 有文本但 last_query_time 为 None 时,
旧代码在 try 之外 AttributeError;现在显式取 0.0。值存在时渲染文本
逐字不变(continuity 字节恒等契约不受影响)。

## 2026-08-20 — 连续性 prompt 不再展示化石 description

`- Description:` 这一行改走 `description_if_unsummarised()`,
**并且出生证退休时整行消失**,不是留一个空标签。

理由:空的 `- Description:` 在 LLM 眼里读作"这条线没有描述",
而那与"不提这件事"是两个不同的断言。

被摘掉的是什么:创建时抄下的触发输入原文,prod 上最长 198,398 字符,
updater 永不重写。它曾经每一轮都进 continuity 的 user_input。
细节与规模见 models 的 mirror 与
`data/replay_runs/2026-08-20/DESCRIPTION_RETIREMENT_DRYRUN.md`。


## Why it exists

Phase 1 of narrative selection (see [[narrative_service.py]]). Given the user's
current message + the session anchor (previous query/response + the current
Narrative's metadata), an LLM decides `is_continuous` — i.e. whether to stay in
the current Narrative or fall through to Phase 2 vector retrieval. Conversation
continuity ≠ same Narrative: the user may keep talking but switch topic.

## 上下游关系
- **被谁用**: `NarrativeService.select()` via `_get_continuity_detector()`.
- **依赖谁**: `OpenAIAgentsSDK` (helper LLM), `CONTINUITY_DETECTION_INSTRUCTIONS`
  prompt, `ConversationSession` / `ContinuityResult` models.

## 设计决策

**Clean anchors in, no stripping** (2026-06-01): `current_query` / `last_query`
/ `last_response` now arrive as clean retrieval anchors (`[From <name>] <body>`)
from `NarrativeService.select` (which reads `retrieval_anchor` off the trigger's
`extra_data`). The old `_extract_core_content` template-stripping (regex over
`[Lark · …]` headers + `[ts] @sender:` history) was **deleted** — its regex had
drifted from the live channel template and stripped nothing in prod (ratio
100%). See the 2026-06-01 embedding-anchor design doc.

## 2026-05-20 — anchor to the last *visible* message (query OR response)

The early-return that treated "no `last_query`" as a brand-new session was
widened: it now returns `new_session` only when **both** `last_query` and
`last_response` are empty. Reason: when the agent messages the user proactively
(e.g. from a scheduled job), the session anchor has `last_response` set but
`last_query` empty — and a short reply ("好"/"yes") is almost certainly
answering that delivered message. `_call_llm` now frames that case explicitly
("the agent messaged the user proactively; the user is most likely replying to
this") instead of emitting an empty "User asked:" line. Pairs with the gate
change in [[narrative_service.py]] and the anchor write in
[[step_4_persist_results.py]] (both 2026-05-20).

## Gotcha / 边界情况

The helper LLM is whatever `CONTINUITY_LLM_MODEL` resolves to (often a small/
fast model); structured output may run in fallback mode. It is a *routing*
judgment, not the agent's reply — keep it cheap, but be aware a weak model can
mis-judge subtle short-reply continuity.
