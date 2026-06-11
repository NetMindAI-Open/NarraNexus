---
code_file: frontend/src/components/awareness/AwarenessPanel.tsx
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 — atomic `section` prop

`section?: 'awareness'|'workspace'|'channels'|'social'` renders exactly
ONE section (bookmark-strip atomic IA: one tab = one content; the
stacked all-sections view remains the default for any legacy callers).
Stat strip only renders with the social section. Edit modal stays with
the awareness section.

## 2026-06-10 — embedded mode

`embedded` prop swaps the Card shell for a plain div and hides the
duplicate title when hosted in the bookmark drawer's
[[AgentProfilePanel]]; Edit/Refresh actions and the internal sections
(Workspace / IM Channels / Social) are untouched. Default rendering
unchanged.

## 2026-05-20 — Save button contrast fix (invisible "black blob")

The awareness edit dialog's Save button was `variant="default"` with a
`className` override forcing `bg-[var(--accent-primary)]`. But `--accent-primary`
resolves to `--nm-ink`, and `variant="default"`'s text color is *also*
`--nm-ink` → background and text were the same color (a black blob in light
mode, a white blob in dark mode), zero contrast. Switched to `variant="accent"`
(the canonical ink-fill primary action: `bg --nm-ink` + `text --nm-paper`,
high-contrast in both themes) and dropped the override. This matches the other
primary-action buttons across the app (RAGUpload, ChatPanel, MCPManager).

## 2026-05-15 — awareness markdown inner-scroll discoverability

Awareness thesis block's inner `<ScrollArea>` now uses `type="auto"` (always-visible scrollbar when overflow exists) and `max-h-[40vh]` instead of the original hover-only `max-h-[180px]`. Combined with `overscroll-contain` becoming a default in `ui/scroll-area.tsx`, the markdown content stays scrollable inside its own viewport without chaining to the outer panel's ScrollArea. Same fix was applied to `FileUpload.tsx`'s workspace tree — both inner-scrolls were invisible for the same reason and shipped together.

# AwarenessPanel.tsx — Agent configuration hub (awareness + workspace + IM + social network)

## 为什么存在

The "Config" tab gives operators visibility into and control over the agent's current state: what it knows about itself (awareness text), what files it can access (workspace), which IM channels it's bound to, and who it knows (social network).

## 2026-05-14 — Section reorder + MCP moved out

Section order changed (user request) to a top-down "agent identity → tools → social" flow:

1. **Agent Awareness** (unchanged, first, no border-t)
2. **Workspace** (`FileUpload`) — moved up from 3rd
3. **IM Channels** (`IMChannelsSection`) — moved up from 5th
4. **Social Network** — moved down from 2nd to last

`MCPManager` was **removed entirely** from this panel and relocated into `[[SkillsPanel]]` (the "Skill & MCP" tab) — MCP servers are a tool/capability concern, not an awareness concern, so they belong next to Skills.

## 上下游关系
- **被谁用**: `ContextPanelContent` (lazy-loaded when 'awareness' tab is active).
- **依赖谁**: `EntityCard`, `FileUpload`, `IMChannelsSection`, `usePreloadStore`, `useConfigStore`, `api`.

## 设计决策

**Awareness text editing**: Done in a `Dialog` modal (not inline) to avoid layout shifts and to provide a proper multi-line edit experience. On save, calls `api.updateAwareness` then re-fetches.

**Social network sort**: Entities are sorted: current user first, then by actual chat count derived from `chatHistoryEvents`. This count is recalculated with `useMemo` each render but the data comes from preloaded `chatHistoryEvents` (no extra API call).

**Semantic vs keyword search**: Both modes call `api.searchSocialNetwork`. Search results replace the sorted list while `hasSearched` is true; the original list reappears after clearing.

**Red dot clearing**: On mount, calls `clearAwarenessUpdate(agentId)` to dismiss the notification dot in the tab header. This means opening the Config tab is treated as "acknowledged".

## Gotcha / 边界情况

`RAGUpload` is imported but not rendered — there's a comment in the source: "RAG Upload Section removed — Gemini RAG deprecated". The import should be cleaned up.

KPI metrics row uses `KPICard` from `@/components/ui` but the network stats are calculated inline here, not from the store.
