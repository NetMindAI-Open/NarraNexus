---
code_file: frontend/src/components/chat/ChatPanel.tsx
last_verified: 2026-04-10
stub: false
---

# ChatPanel.tsx — Unified timeline chat surface with streaming and history pagination

## 为什么存在

The primary user-facing interface. All agent interaction goes through here. Merges two data sources (DB history and live WebSocket session) into a single chronologically ordered `TimelineItem[]` so the user sees one seamless conversation regardless of how many messages have been paginated or how the current run is progressing.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: `MessageBubble`, `EmbeddingBanner`, `useChatStore`, `useConfigStore`, `useAgentWebSocket`, `api.getSimpleChatHistory`.

## 设计决策

**Unified timeline**: History messages and session messages are merged and sorted by timestamp. Dedup is done by content key (`role:content`) against the last 30 history items — not by timestamp — because frontend `Date.now()` vs backend UTC can diverge by enough to cause false duplicates. This means identical consecutive messages from the same role could theoretically be deduped incorrectly, but this is rare in practice.

**Polling**: A 12-second interval polls for new background messages (from non-chat agent runs like Jobs). It only replaces the tail of history to avoid losing scroll position for users who've loaded older messages.

**Auto-load when not scrollable**: If the initial history page doesn't fill the container, the panel automatically calls `loadMoreHistory` until the container is scrollable. This prevents the "infinite scroll trigger never fires" problem when messages are small.

**IME handling**: The send button is gated by `isComposing` and a 100ms grace period after `compositionend`. Without this, CJK input methods would fire Enter before the character is committed.

**Bootstrap greeting**: If `bootstrap_active` is true and there are no messages, the panel renders a hard-coded bootstrap greeting. The greeting content is kept in sync with `src/xyz_agent_context/bootstrap/template.py` — comment in the code flags this dependency.

**`send_message_to_user_directly` filtering**: Tool calls with this name are filtered out of the streaming step preview — they produce the main message content, not a tool activity row.

## Gotcha / 边界情况

`flushSync` is used when prepending older messages after "load more" — this forces React to update the DOM synchronously before the scroll position is restored. Without `flushSync`, the scroll restoration would measure the old `scrollHeight`.

The `shouldAutoScrollRef` is the gating mechanism for scroll behavior. User scrolling up disables auto-scroll; new messages re-enable it; streaming start re-enables it.

## 新人易踩的坑

`BOOTSTRAP_GREETING` must be kept in sync with the Python backend constant. It's a frontend-only rendering shortcut — the greeting is never actually stored as a chat message until the user replies.
