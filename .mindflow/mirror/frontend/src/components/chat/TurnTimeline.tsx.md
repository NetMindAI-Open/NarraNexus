---
code_file: frontend/src/components/chat/TurnTimeline.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 (r2) — two-tier styling: ANSWER vs PROCESS

The first 2026-05-14 pass ("make Thinking dimmer, Reply larger") was a
**no-op for settled content** and fixed the wrong pair. Two root causes:

1. **`.markdown-content` override.** Settled `thinking` / `reply` bodies
   render through `<Markdown>`, whose `.markdown-content` rule sets an
   explicit `color` and `font-size`. Those win over any ancestor utility
   class — so `text-[var(--text-tertiary)]` on the ThinkingBlock
   container and `text-[15px]` on the ReplyBlock body reached only the
   label + the brief streaming plain-text path, never the settled body.
2. **Wrong pair.** The hard-to-tell-apart pair was Thinking ↔
   **NativeOutput** (both muted, both dashed-tertiary border), not
   Thinking ↔ Reply. `native_output` had never been touched.

r2 reworks all three "speech-ish" blocks into **two semantic tiers**,
keyed by border style:

- **ANSWER tier — SOLID left rule** (content the user should read):
  - `reply` — peak: thick solid *accent* rule + faint accent fill +
    accent label + body one notch larger.
  - `native_output` — same tier, one notch below: solid *secondary*
    rule, full-strength body, neutral tone, no fill. (Was: dashed
    tertiary + `opacity-80` — i.e. visually identical to thinking.)
- **PROCESS tier — DASHED left rule** (skimmable):
  - `thinking` — dashed tertiary rule, dimmest tone throughout.
  - `tool_call` / `tool_output` — unchanged mono affordances.

**The override is now defeated properly:** two `markdown-*` variant
classes in `index.css` (`.markdown-content.markdown-dim`,
`.markdown-content.markdown-reply`) — two-class selectors, specificity
0,2,0, beat `.markdown-content`'s 0,1,0. `ThinkingBlock` passes
`className="markdown-dim"` and `ReplyBlock` passes
`className="markdown-reply"` to `<Markdown>`. `native_output` never uses
Markdown so its container styling applies directly.

Order / position / event logic unchanged — styling only.

# TurnTimeline.tsx — Inline event timeline for a streaming agent turn

## 为什么存在

Before this component the chat panel had two parallel renderings of
the current turn:

- a streaming MessageBubble (thinking and tool calls collapsed inside,
  shown only after the first send_message)
- a "Live activity preview" italic stream + spinner list of tool steps

That double-rendering grouped events by *kind* (all thinking together,
all tools together) instead of by *time*. With multiple tool calls
thinking was pushed out of view and the user couldn't see the actual
rhythm of "think → tool → think → tool → reply → think". Xiong called
this out in the 5/11 review.

`TurnTimeline` replaces both of the above. It renders one block per
event in chronological order, so the user reads exactly what the
agent was doing at each moment.

## 上下游关系

- **被谁用**: `ChatPanel.tsx` — for both the currently streaming turn
  (`currentEvents`) and the just-completed turn that hasn't been
  collapsed yet (`lastTurnEvents`).
- **依赖谁**: `chatStore.processMessage` (which builds the events
  array out of the raw websocket frames); `@/types/messages.TurnEvent`
  (the discriminated union); `Markdown` (reply rendering).

## 设计决策

**One block per event, no grouping by kind**. Thinking → tool → think →
reply → think appears in exactly that visual order. This is the whole
point — it answers the "what is the agent doing now" question.

**Visual hierarchy expresses speech vs. thought**:
- Reply blocks get a coloured left border, bigger type and full markdown
  rendering — they are the user-facing speech.
- Native_output gets a dashed left border and muted styling — it is the
  agent's text that wasn't routed through the reply tool, so it sits
  one notch below a real reply visually.
- Thinking gets italics, smaller type, muted colour and a subtle left
  rule — it is internal monologue and should not compete with replies.
- Tool calls are a single line in a pill — the *what* (tool name +
  one-line arg preview) is enough for chat context; full args / output
  belong on the right-side Execution panel.

**Per-block expand/collapse with local state**. Each block keeps its
own `useState` for expanded. Because the parent ChatPanel keeps the
same TurnTimeline mounted across renders during a turn (events only
append), `key={event.id}` preserves state correctly. Reload of the
page unmounts the component → state resets → first-open default
(collapsed for long thinking, collapsed for tool args, expanded for
reply) — matches the "本次展开保持，下次打开折叠" rule from the 5/11
review.

**Friendly tool names**: `mcp__chat_module__get_chat_history` is
stripped of the MCP prefix and shown as `get_chat_history`. The full
canonical name is still in the underlying event for debug logs.

**helper_llm fallback indicator**: when a reply event carries
`reply_via === "helper_llm_fallback"` (i.e. the agent didn't call
send_message and we synthesised a reply via helper_llm in
step_3_agent_loop), a small "↻ helper_llm fallback" tag appears above
the reply. This is an operator-facing breadcrumb, not a user warning;
the reply content itself is normal.

## Gotcha / 边界情况

- Empty events array renders nothing (returns `null`). The "Starting
  up..." indicator is the parent's responsibility — see ChatPanel.
- Long thinking blocks are clipped to 280 chars with a "show full"
  toggle; the goal is to keep reply blocks visible without scrolling
  past several paragraphs of reasoning. If a reasoning model emits
  multi-thousand-character thinking this might still feel heavy —
  see follow-up TODO #3 in mindflow tasks.
- `Markdown` is imported for reply rendering only; thinking and
  native_output deliberately use plain `whitespace-pre-wrap` because
  they are not user-facing speech and shouldn't be elevated by
  formatting.

## 新人易踩的坑

- Adding a new event type: extend `TurnEvent` in
  `frontend/src/types/messages.ts` AND add the case in `TurnTimeline`
  AND add a push in `chatStore.processMessage`. Forgetting any one of
  the three silently drops events.
- The "skip the most-recent session assistant message in the unified
  timeline" logic lives in `ChatPanel.tsx::timeline useMemo`, not here.
  Without it, the just-completed reply would render twice (once as a
  history bubble and once as a reply block in this component).
