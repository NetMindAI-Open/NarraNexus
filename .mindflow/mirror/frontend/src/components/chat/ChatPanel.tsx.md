---
code_file: frontend/src/components/chat/ChatPanel.tsx
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 语音不可用弹窗：free_tier_opted_out → free_tier_not_granted

后端 transcription 枚举改名的前端半边（[[service|transcription/service]]）：
402-录音兜底分支设置的 reason 字符串、弹窗分支判断、以及文案——旧文案第二
条"Re-enable 'Use free quota' in Settings → Quota"指向已删除的开关，整条
删除，只留"加 OpenAI/NetMind key"一条路径。review 二轮：删剩单项的 `<ul>`
铺平成一句话；402 兜底分支的注释("opted out"/"toggle flipped")一并去
开关化。

## 2026-07-15 — pass `actionReason` into MessageBubble props

The `TimelineItem` → `MessageBubble` prop mapping now forwards `actionReason`
alongside `isError`/`warnings`. Without it, a `config_actionable` failure lost
its reason on this last hop and the bubble fell back to the generic "Run
failed" popover instead of the actionable "what you can do" panel. See the
upstream carry in [[buildTimeline.ts]].

## 2026-07-10 — history reload reacts to wipe

The history-load effect now also depends on `chatStore.historyRefreshTick`
(see [[chatStore.ts]]), so a data wipe ([[AgentList.tsx]]) forces an immediate
re-fetch — otherwise the locally-cached `historyMessages` only reloaded on
agent switch and the panel showed stale (already-deleted) messages. The poll
loop can't cover this: it early-returns when the server returns zero messages,
so it never clears a now-empty history on its own.

## 2026-07-09 — hosts the per-agent model/framework panel

ChatPanel now owns the per-agent [[AgentLlmConfigPanel]]: a ⚙ (SlidersHorizontal)
ghost icon button sits in the header's right cluster, LEFT of the cost chip
([[CostPopover]]), and opens it. The panel is rendered once here (state
``agentCfgOpen``); on save it bumps ``modelReloadKey``, passed to
[[ComposerModelBadge]] so the composer's quick-switch chip re-reads the model.
The header icon is the single entry point for the detailed settings — the
composer chip stays only the quick model switch.

## 2026-07-03 — pass error state to MessageBubble

The MessageBubble message prop now forwards item.isError + item.warnings, so
the bubble's error rendering (red badge + red bubble + warnings) actually
receives data. Without this the fields died at the ChatPanel boundary even
after buildTimeline started carrying them.

## 2026-07-03 — Inner Thoughts auto-scrolls to bottom (newest activity)

A dedicated effect (keyed on chatTab + the count of activity items) snaps
scrollContainerRef to the bottom when the Inner Thoughts tab opens and when a
new activity arrives — the tab behaves like a chat log (newest at the bottom,
visible without a manual scroll-down), not an inbox. Reuses the existing
scroll container; the streaming/initial-scroll effects are untouched.

## 2026-07-03 — Inner Thoughts rows are InnerThoughtCard

The activity branch of the timeline map (Inner Thoughts tab, ``messageType
=== 'activity'``) previously rendered a single centred 10px italic line
(content + time). It now renders ``<InnerThoughtCard item agentId />`` — a
source-labelled, expandable card whose expanded region lazily loads the
turn's agent-loop steps by event_id. ``agentId`` comes from the component's
``useConfigStore()`` (already in scope). See InnerThoughtCard.tsx.md.

## 2026-06-25 — two chat tabs: Conversation | Inner Thoughts

A `chatTab` state (`'conversation' | 'inner'`) with a tab bar under the header.
The agent runtime already tags output distinctly, so this is pure frontend
routing — no backend change.

- **Conversation**: the original design, unchanged — full reply bubble + the
  inline "reasoning & tools" disclosure + the live streaming `TurnTimeline`
  (+ "starting up" indicator). It just stops rendering the items that belong to
  Inner Thoughts.
- **Inner Thoughts**: **only** the lightweight background-activity markers —
  `messageType:'activity'` → compact centered line ("Background activity
  (discord)"). Nothing else. No live stream here (post-hoc feed); streaming
  stays in Conversation.

> **2026-06-25 correction.** An earlier version *also* routed any turn whose
> `workingSource` wasn't `chat`/`manyfold` (discord / slack / lark / job / …)
> into Inner Thoughts, on the theory that cross-channel narrations
> (`owner_notify_content`, "I replied to a Discord user / notified you") were
> "the agent's own activity". That was wrong: those narrations are emitted via
> `send_message_to_user_directly` — the agent **deliberately addressed the
> owner**, so they are owner-facing messages and must show in Conversation no
> matter which channel triggered the turn. Routing now keys on `isActivity`
> alone; `workingSource` does not move a message out of the direct
> conversation. `MessageBubble` is unchanged. Routing: `if (chatTab === 'inner'
> ? !isInner : isInner) return null;`.

## 2026-06-20 — design-ref pass: binding-dot header, JourneyBand empty state, Connected footer

Three changes aligning with the Narra Agent App design ref:

- **Header**: the lone `StatusDot` is replaced by [[identity|BindingDot]]
  (carbon·silicon motif) before the `[ Interaction <agent> ]` label; it
  `pulse`s while streaming, keeping the live cue the StatusDot carried.
- **Empty state**: with an agent selected, the generic "Start a conversation"
  bracket is replaced by [[OnboardingJourney]] (binding-dot eyebrow,
  memory→network→team stations, suggested-prompt chips). With NO agent it still
  shows the plain `BracketEmptyState` ("Select an agent"). Note the brand-new
  unnamed-agent path is unchanged — `showBootstrapGreeting` (BOOTSTRAP_GREETING
  "I just woke up" bubble) takes precedence over `showEmptyState`, so the two
  never collide.
- **Composer footer**: briefly carried Enter/Shift+Enter/Drop hints + a
  readiness indicator, but both were **removed** (clawcreek-style minimal
  composer). The send button now uses the `CornerDownLeft` (↵) glyph and a
  `title="Send (Enter)"` so the button itself signals "Enter sends" — no
  separate hint row. (StatusDot/Kbd imports dropped with it.)

Suggested-prompt chips call `composerRef.current.setText(...)` (see
[[Composer]]) — fill, don't send.

## 2026-06-11 (v1.8.1) — clickable Processing chip + header truncation

The Processing indicator is now [[ExecutionPopover]] — click opens a
live pipeline-step list (the execution view retired with RuntimePanel,
resurrected as click-to-peek). Header left side gained
overflow-hidden + agent-id truncation so a narrow chat (artifact
column open) can never run the label under the Processing/cost cluster.

## 2026-06-11 — CostPopover joins the header row

The cost chip used to float `absolute top-2 right-2` over the chat
card (MainLayout) and collided with this header's Processing indicator
during runs. It is now a proper flex member of the header's right
side, next to Processing — no overlap possible. Carries the
`chat.cost` help anchor.

## 2026-05-29 — defer streaming values to throttle render bursts (F5)

The five high-frequency streaming values from chatStore
(currentAssistantMessage / currentThinking / currentSteps /
currentToolCalls / currentEvents) are read into `_rt*` locals then wrapped
in `useDeferredValue` so React coalesces a streaming storm into fewer
commits while always converging to the latest value (iron rule #16:
throttle render rate, never drop/reorder content). `messages` stays
immediate (the timeline dedup depends on it). Pure render-scheduling —
the chatStore delta-merge logic is untouched. Effect (fewer renders under
load) is only observable via real-browser profiling.

## 2026-05-22 — chat input extracted to <Composer> (typing-lag fix)

The message textarea + its draft text used to be `input` state living right
here. Because this component subscribes to the **whole** chat store
(`useChatStore()` with no selector) it re-renders on every streaming delta;
with `input` also here, every keystroke re-rendered this 1300-line monolith,
and typing *while an agent streamed* (esp. one-char-per-token models) made the
two re-render storms collide → laggy input.

The text now lives in `Composer.tsx`. ChatPanel reads it imperatively on send
(`composerRef.getText()`), clears it after a successful send
(`composerRef.clear()`), and tracks only the empty↔non-empty flip
(`composerEmpty`) for the Send button. The drag/paste handlers are passed down
as **stable** wrappers (`stableSubmit`/`stableDrag*` via a ref) so the memoized
Composer doesn't re-render when ChatPanel does. Draft persistence (was a
per-keystroke synchronous localStorage write) is now debounced inside Composer.
`key={agentId}` remounts Composer on agent switch to restore that agent's draft.
铁律 #16: pure render isolation — no message content is dropped or throttled.

## 2026-05-20 — streaming avatar: Bot icon → name-driven RingAvatar

Both in-flight streaming rows (the "events arriving" branch and the
"Starting up…" branch) used to render a hardcoded lucide `<Bot>` icon as the
left avatar — the "old robot" that didn't match the agent's real identity or
the historical `MessageBubble` avatar. Replaced both with
`<RingAvatar species="silicon" label={(currentAgent?.name || agentId || 'AI').slice(0, 2)} />`,
so the live turn shows the same name-initial avatar as finished turns and the
sidebar. Also threads `agentName={currentAgent?.name || agentId}` into
`MessageBubble` (see its mirror md). `Bot` import dropped (now unused).

## 2026-05-15 — artifact card → inline badge

`ArtifactToolCallCards` no longer renders `<ArtifactPreviewCard>` (the full-sized thumbnail with CSV/image/markdown previews). It now emits one `<ArtifactInlineBadge>` chip per **unique** artifact_id in the turn. Re-register on the same artifact is deduped down to a single badge. The card was visually disruptive (re-registers re-mounted it, producing a "flash and disappear" feeling) and the right-side ArtifactColumn is the canonical place to view content — the badge is just an affordance to jump there. ArtifactPreviewCard is kept in `components/artifacts/` for potential future re-use but is no longer mounted from chat.

## 2026-05-15 — re-register signal: refetch (not ensure-loaded)

`ensureArtifactLoaded` (which short-circuited on "already in store") was
replaced with `refreshArtifactFromToolCall(agentId, artifactId, dedupKey)`.
Reason: a `register_artifact` call with `target_artifact_id=<existing>` is
the agent's refresh signal — same `artifact_id` arrives in the tool stream
but with a bumped `updated_at`. The old guard would skip the fetch and
renderers would never see the new timestamp, so the iframe wouldn't
reload. The new helper always refetches, deduped per tool call by a key
built from `tc.step + tc.tool_output` so the render loop doesn't trigger
infinite refetches. The seen-Set is module-scope (small bounded growth
per session, no leak concern).

## 2026-05-14 — artifact tool name collapsed to `register_artifact`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`ARTIFACT_TOOL_BASE_NAMES` is now `['register_artifact']` (was
`['create_artifact', 'upload_artifact_file']`). The frontend's live artifact
discovery keys off this list to recognise tool calls in the agent stream
and surface `ArtifactPreviewCard`s — must stay in lockstep with the
`@mcp.tool(name=...)` registration in `artifact_tool.py`. Also updated the
`ensureArtifactLoaded` helper because `artifactsApi.getDetail` now returns
`Artifact` directly (no `{artifact, versions}` wrapper).

## 2026-05-14 — timeline dedup extracted; event_id-based dedup

The unified-timeline merge + dedup (a ~50-line block inside the `timeline`
`useMemo`) was extracted into the pure, unit-tested
`[[buildTimeline.ts]]` — `buildUnifiedTimeline(historyMessages, messages)`.
The `TimelineItem` type moved there too; ChatPanel imports both.

The dedup itself was upgraded: **`(role, event_id)` exact match** instead
of the old `${role}:${content}` exact-string key. The string key missed
whenever the session-assembled content drifted from the DB-persisted
content by even one whitespace char (two independent code paths) — that
was the "latest reply shown twice" bug. Session messages now carry
`event_id` (stamped by `[[chatStore.ts]]`); see `[[buildTimeline.ts]]`
for the full dedup contract. The old `role:content` + window + consume
logic survives only as the fallback for event-id-less messages.

The "Match-and-consume semantics" / "5 min window" notes in the v2.4
section below still describe the **fallback** path accurately, but the
primary path is now event_id.

## 2026-05-14 — artifact tool-name matching must tolerate the `mcp__…__` prefix

**Bug:** the artifact panel never updated during/after a run — the
artifact only appeared on an unrelated reload (agent switch). Root cause
was here: MCP tools arrive in the event stream **fully-qualified** —
`mcp__<server>__<tool>`, e.g. `mcp__common_tools_module__create_artifact`
— but the code matched a bare-name `Set` exactly
(`ARTIFACT_TOOL_NAMES.has(tc.tool_name)`). That `.has()` never returned
true, so `hasArtifactTools` was always false, `ArtifactToolCallCards`
never rendered, and `ensureArtifactLoaded` never fired.

**Fix:** replaced the exact-match `Set` with `isArtifactToolName()` —
matches the bare name OR a `…__<base>` suffix, so both qualified and
unqualified forms work. `ARTIFACT_TOOL_BASE_NAMES` must stay in sync
with the MCP tool names registered in `common_tools_module` — there is a
reciprocal comment on the tool implementations in `[[artifact_tool.py]]`
flagging this coupling.

(Sibling fix: `tool_output` itself must be clean JSON for the
`JSON.parse` here to work — see `[[output_transfer.py]]`.)

## 2026-05-13 — Phase C: 自动 reconnect 到后端在跑的 run

新增 useEffect 监听 `agentId + userId + currentAgent.active_run.run_id`：
当用户打开（或切换到）一个已经在后端跑着的 agent，前端立刻调
`reconnect(agentId, userId, activeRunId, agentName)`，让 `wsManager`
重开一条带 `run_id` 的 WS。后端识别到 run_id 就走 replay 分支：把
event_stream 里所有 seq ASC 的事件回放完，再 hook 到 broadcaster
拿 live 接续。

业内对这种模式的标准说法（用户在最近一次对话里直接问到）：
**resumable / replayable streaming session**——event_stream 是事件
存储（event sourcing），server-side run 是 long-running operation
(LRO)，WS reconnect = last-event-id-style resumption（W3C SSE 把它
做成 first-class，我们在 WS 上等价实现），整体是 "server-side
session continuity"。

useEffect 的边界条件（顺序 short-circuit）：
1. 没 agentId / userId → 直接返回（panel 还没 ready）
2. `activeRunId` 为 null → 后端没活跃 run，不重连（也是退出条件
   防止 run 结束后死循环）
3. 本地 `isLoading=true` → 当前 tab 自己刚发完 fresh-run 还在跑，
   wsManager 已经管理着一条 WS，**不能**再开一条；reconnect 也
   不需要——本地路径已经在收 live frames
4. 上述都不满足 → fire-and-forget `reconnect()`；`wsManager` 内部
   保证 idempotent（开新连接前 close 旧的）

依赖数组 `[agentId, userId, activeRunId]` 是关键：
- 用户切换 agent：activeRunId 跟着 currentAgent 变化（可能变 null
  或变成新 agent 的 run_id），effect 重跑
- run 结束后 `/api/auth/agents` 下一次拉到 active_run=null，
  activeRunId 变 null，effect 重跑后第 2 步退出 —— **不会**继续
  连旧 run

`reconnect` 故意从 deps 里排除（eslint-disable-next-line）——它在
hook 里是 useCallback 包过的稳定引用，写进去只会徒增噪音。

## 2026-05-11 fix — live activity stays visible after first reply (P0)

The streaming-state UI used to have two mutually exclusive render
branches:

1. `isStreaming && getUserVisibleResponse()` → render a streaming
   `MessageBubble` with the reply content. `thinking` and `toolCalls`
   are passed in but live inside the bubble's **collapsed** Reasoning
   / Tool-calls sections (`MessageBubble.tsx` initialises `showThinking`
   and `showTools` to `false`).
2. `isStreaming && !getUserVisibleResponse()` → render the "Live
   activity preview": italic streaming `currentThinking` text + a
   spinner-decorated list of in-flight `toolSteps`. **Always visible,
   no click required.**

The instant the agent called `send_message_to_user_directly` for the
first time, `getUserVisibleResponse()` flipped from `null` to a
string, branch 2 unmounted, and branch 1 took over. Any subsequent
thinking deltas or tool calls kept accumulating into
`chatStore.currentThinking` / `currentToolCalls` but had **no
always-visible UI surface** — the reply bubble looked finished even
when the agent was still mid-loop running more tools. Xiong's P0
"先回复一条信息后，不再显示思考过程" (`recvjhejbs2abv`).

Fix: drop the `!getUserVisibleResponse()` gate so the live activity
preview now stays mounted for the **entire** streaming window. The
streaming MessageBubble keeps receiving `thinking` / `toolCalls` so a
user who clicks "Reasoning" mid-stream still sees the full trace; the
live preview below provides the always-visible "still working" signal
until `stopStreaming` flips `isStreaming` to false (at which point the
bubble persists into history with its data already attached, line
269-270 of `chatStore.ts`). The `toolSteps` filter (regex
`/^3\.4\.\d+$/` minus `*.send_message_to_user_directly`) intentionally
drops the reply tool call so the same action doesn't appear twice
(once as the bubble, once as a tool step).

Defensive guard: inside the live preview, the `!hasActivity` fallback
now renders the "Starting up..." banner only when
`!getUserVisibleResponse()`. Without this, an LLM that emits no
`agent_thinking` deltas and whose only progress step is the
send_message tool call itself (which `toolSteps` filters out) would
land on `hasActivity=false` *after* a reply already rendered above —
visually contradicting "Starting up..." beneath a populated reply
bubble. With the guard, the live preview cleanly disappears in that
rare path instead.

# ChatPanel.tsx — Unified timeline chat surface with streaming and history pagination

## 为什么存在

The primary user-facing interface. All agent interaction goes through here. Merges two data sources (DB history and live WebSocket session) into a single chronologically ordered `TimelineItem[]` so the user sees one seamless conversation regardless of how many messages have been paginated or how the current run is progressing.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: `MessageBubble`, `EmbeddingBanner`, `useChatStore`, `useConfigStore`, `useAgentWebSocket`, `api.getSimpleChatHistory`.

## 设计决策

**Unified timeline**: History messages and session messages are merged and sorted by timestamp. Dedup is done by `role:content` key + 60-second timestamp-proximity check. **Match-and-consume semantics (Bug 19 fix)**: once a session message pairs with a history timestamp, that timestamp is spliced out of the per-key array so it can't dedup another session message with the same role+content. Without consumption, a single history row would gobble multiple session messages — realistic trigger is "user retries the exact same question after a failed turn", which would silently drop the retry bubble from the UI. Plan B (event_id-based precise dedup) is logged in `reference/self_notebook/todo/waiting/chat_dedup_by_event_id.md` as a future upgrade.

**Polling**: A 12-second interval polls for new background messages (from non-chat agent runs like Jobs). It only replaces the tail of history to avoid losing scroll position for users who've loaded older messages.

**Auto-load when not scrollable**: If the initial history page doesn't fill the container, the panel automatically calls `loadMoreHistory` until the container is scrollable. This prevents the "infinite scroll trigger never fires" problem when messages are small.

**IME handling**: The send button is gated by `isComposing` and a 100ms grace period after `compositionend`. Without this, CJK input methods would fire Enter before the character is committed.

**Bootstrap greeting**: If `bootstrap_active` is true and there are no messages, the panel renders a hard-coded bootstrap greeting. The greeting content is kept in sync with `src/xyz_agent_context/bootstrap/template.py` — comment in the code flags this dependency.

**`send_message_to_user_directly` filtering**: Tool calls with this name are filtered out of the streaming step preview — they produce the main message content, not a tool activity row.

## Gotcha / 边界情况

`flushSync` is used when prepending older messages after "load more" — this forces React to update the DOM synchronously before the scroll position is restored. Without `flushSync`, the scroll restoration would measure the old `scrollHeight`.

The `shouldAutoScrollRef` is the gating mechanism for scroll behavior. User scrolling up disables auto-scroll; new messages re-enable it; streaming start re-enables it.

**Two-mode scroll (Bug 15)**: scroll-to-bottom is split into two effects because "initial open" and "streaming tick" have incompatible requirements. `initialScrollPendingRef` is raised whenever fresh content arrives (initial load, agent switch, background poll, user's own submitted message). A dedicated effect picks it up, waits one `requestAnimationFrame` so `MessageBubble` subtrees (markdown, code blocks, tool-call UI) get a frame to lay out, then snaps `container.scrollTop = container.scrollHeight` — instant, not smooth, and scoped to `scrollContainerRef` only (scrollIntoView on a sentinel would also scroll ancestor containers). The streaming effect uses the classic smooth `scrollIntoView` + sentinel, gated by `isStreaming`, because during streaming the deltas are small and smooth feels right. If you ever need to "jump to bottom" from a new code path, set `initialScrollPendingRef.current = true` — do NOT reach for `scrollIntoView` directly (smooth loses the race against async content layout; that was the Bug 15 root cause).

## v2.4 改动（2026-05-08）— Inline artifact preview cards

- **`ArtifactToolCallCards` component**: a file-local component that receives `toolCalls: AgentToolCall[]`, `agentId`, and `allArtifacts` (pre-read from the store at component scope — not inside the map callback). For each tool call where `tool_name ∈ {create_artifact, upload_artifact_file}` and `tool_output` parses as JSON with an `artifact_id`, it renders an `ArtifactPreviewCard`. While the artifact is not yet in the store, it shows a "Loading artifact…" placeholder and fires `ensureArtifactLoaded` (fire-and-forget fetch → upsert).
- **`ensureArtifactLoaded` helper**: a module-level function (not a hook) that checks `useArtifactStore.getState().artifacts` for the given `artifact_id`. If absent, calls `artifactsApi.getDetail` and upserts the result. Safe to call on every render because the store lookup short-circuits immediately when already cached.
- **Hook rule compliance**: `allArtifacts` is read via `useArtifactStore((s) => s.artifacts)` at the `ChatPanel` component scope (top-level hook call), then passed down as a prop. This avoids calling a hook inside the `timeline.map()` callback.
- **Placement**: `ArtifactToolCallCards` is rendered as a sibling of `MessageBubble` inside each timeline item's wrapper `<div>`, so the cards appear below the message bubble.

## 新人易踩的坑

`BOOTSTRAP_GREETING` must be kept in sync with the Python backend constant. It's a frontend-only rendering shortcut — the greeting is never actually stored as a chat message until the user replies.

**Artifact preview placement**: the `ArtifactToolCallCards` render is gated by `hasArtifactTools`, which checks `item.role === 'assistant'`, `agentId` being truthy, and at least one qualifying tool call. This prevents the component from mounting on user messages or when `agentId` is not yet set. The `allArtifacts` dependency means the cards re-render when the store updates (e.g., after `ensureArtifactLoaded` upserts the fetched artifact), replacing the placeholder with the real card automatically.
