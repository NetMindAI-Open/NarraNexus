---
code_file: frontend/src/stores/bookmarkStore.ts
last_verified: 2026-06-11
stub: false
---

# bookmarkStore.ts — Per-agent ambient state for the right-edge bookmark strip

## 2026-06-11 — visibleSubBookmarks removed

The overflow-aggregation helper died with dynamic small bookmarks
(atomic-IA revision: tabs are static; signals render as status
overlays via [[tabs]] deriveTabStatus). Signal actions and the
two-layer reset/badge semantics are unchanged.

## 为什么存在

The bookmark-strip redesign (spec 2026-06-10, Workstream A) replaces the
permanent 5-tab context panel with a collapsed edge strip whose big/small
bookmarks light up when something changes. That needs a dedicated ambient
state layer: which items are highlighted, which sub-bookmarks exist, and
which counters demand user action. None of the existing stores own this —
chatStore owns streaming, preloadStore owns panel data caches.

## 上下游关系

- **被谁写**: `chatStore.processMessage` (`run_started` → `onRunStart`);
  M3's signal-collection hook will feed `noteJob*` / `noteInboxUnread` /
  `noteProfileUpdate` from preloadStore/jobs data.
- **被谁读**: M3's `BookmarkStrip` / `BookmarkDrawer` (visibleSubBookmarks,
  highlights, badges).

## 设计决策（核心：双层语义，spec §5）

- **Highlight layer resets at NEW-run start** (Owner decision). Reset
  happens when the next `run_started` frame arrives — NOT at run end — so
  the post-run idle period keeps the last run's marks visible for the
  user to inspect. `run_started` is the single wiring point: it is the
  authoritative new-run signal and is never emitted on a Phase C
  reconnect (`run_reconnect`), so a same-run reconnect can never wipe
  highlights.
- **Badge layer is exempt from the reset** (iron rule #16 spirit —
  collapsing/resetting must not lose actionable info). Failed jobs and
  inbox unread keep their badge AND their sub-bookmark across runs until
  the user actually handles them (`resolveJobAttention`, inbox count→0).
- **In-memory only, no persistence.** Bookmarks are ambient signals
  derived from live data; persisting them would serve stale highlights
  after a reload for no user value.
- `visibleSubBookmarks(state, max=3)` is a pure exported helper (spec §4
  aggregation: priority running > attention > info, overflow collapses
  into a `+k` entry) so the strip and tests share one derivation.
- Per-agent isolation: state is `Record<agentId, AgentBookmarkState>`;
  the strip always renders the selected agent's slice.

## 新人易踩的坑

`markOpened` clears only 'info' highlights. 'attention' highlights are
cleared exclusively by resolving the underlying condition — opening the
drawer to look at a failed job does not make it less failed.
