---
code_file: frontend/src/hooks/useBookmarkSignals.ts
last_verified: 2026-06-10
stub: false
---

# useBookmarkSignals.ts — Store-watcher that feeds the bookmark layer

## 为什么存在

The bookmark strip is ambient UI and must not own data fetching. This
hook is the single translation point from "what the data stores know"
(preloadStore jobs + inbox count, configStore awareness updates) to
"what the strip shows" ([[bookmarkStore]] notes). Mounted once by
MainLayout's ChatView.

## 设计决策

- **Job transitions, not states**: completed only counts as news on an
  observed in-session transition (historical completions on first load
  would spam stale info highlights). Failed counts even on first load —
  an unresolved failure is actionable backlog, not news.
- A job leaving the failed state WITHOUT going through the panel's
  cancel/resume buttons (auto-retry succeeded, external action) calls
  `resolveJobAttention` — the badge must track reality, not UI clicks.
- The prev-status map resets on agent switch because preloadStore's
  jobs array is already scoped to the selected agent.

## 新人易踩的坑

Inbox unread flows through here AND through AgentInboxPanel's own
mark-read path; bookmarkStore.noteInboxUnread(count=0) is what clears
the badge — driven by the store count, never by the UI directly.
