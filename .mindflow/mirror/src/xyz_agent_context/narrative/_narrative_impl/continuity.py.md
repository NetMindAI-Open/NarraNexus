---
code_file: src/xyz_agent_context/narrative/_narrative_impl/continuity.py
last_verified: 2026-06-01
stub: false
---

# continuity.py — LLM-based "does this query continue the current Narrative?"

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
