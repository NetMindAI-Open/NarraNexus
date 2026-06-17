/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure:
 * ┌──────────┬──────────────────────┬──┬──────────────────┬──────────────────┐
 * │          │                      │║│                  │ [Tab] [Tab] [Bell]│
 * │  Agent   │      Chat Area       │║│ Artifact Column  ├──────────────────┤
 * │  List    │                      │║│ (auto-hides when │                  │
 * │          │  (Spacious chat)     │║│   no artifacts)  │  Context Panel   │
 * │          │                      │║│                  │  (Tab content)   │
 * └──────────┴──────────────────────┴──┴──────────────────┴──────────────────┘
 *                                    └─ Drag handle (chat ↔ artifacts)
 *
 * Right-side tabs: Runtime, Awareness, Agent Inbox, Jobs
 * Top-right bell: User Inbox Popover
 * Artifact column: auto-hides when no artifacts; collapses to sliver on demand.
 *
 * Chat ↔ Artifacts split is user-resizable via the ResizableDivider; ratio
 * persisted to localStorage. Divider hidden when the artifact column is in
 * sliver mode (no artifacts yet, or user-collapsed) because resizing a
 * 36-px sliver is meaningless.
 *
 * Signal source: artifact_id signals arrive via the chat WebSocket stream
 * (tool_output frames parsed in ChatPanel.tsx). loadPinned is called on mount /
 * agent change to hydrate agent-scoped artifacts. No dedicated artifact WS.
 */

import { useState, useEffect, useRef, useCallback, Suspense } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ResizableDivider } from './ResizableDivider';
import {
  BookmarkStrip,
  BookmarkDrawer,
  BookmarkPanelHost,
  tabLabel,
} from '@/components/bookmarks';
import type { AtomicTabId } from '@/components/bookmarks';
import { HelpButton, CHAT_VIEW_PAGES } from '@/components/help';
import { useBookmarkSignals } from '@/hooks/useBookmarkSignals';
import { ChatPanel } from '@/components/chat';
import { OnboardingChecklist } from '@/components/onboarding/OnboardingChecklist';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { ArtifactColumn } from '@/components/artifacts';
import { useConfigStore, usePreloadStore, useArtifactStore } from '@/stores';
import { useAutoRefresh } from '@/hooks';

const SPLIT_STORAGE_KEY = 'chat_artifact_split_v1';
const DRAWER_PINNED_KEY = 'bookmark_drawer_pinned_v1';
const DRAWER_OPENED_ONCE_KEY = 'bookmark_drawer_opened_v1';
const DEFAULT_SPLIT = 0.6; // 60 % chat, 40 % artifacts — matches the legacy 3:2 flex shares.
const MIN_CHAT_PX = 400;
const MIN_ARTIFACT_PX = 320;
const SPLIT_HARD_MIN = 0.1;
const SPLIT_HARD_MAX = 0.9;

function readInitialSplit(): number {
  if (typeof window === 'undefined') return DEFAULT_SPLIT;
  const raw = window.localStorage.getItem(SPLIT_STORAGE_KEY);
  if (!raw) return DEFAULT_SPLIT;
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_SPLIT;
  return Math.min(SPLIT_HARD_MAX, Math.max(SPLIT_HARD_MIN, parsed));
}

/** Default chat view with context panel */
export function ChatView() {
  // Bookmark drawer: which atomic tab is open (null = closed) and whether
  // the drawer is pinned into a static column (persisted — pinning is a
  // deliberate workspace choice). One tab = one panel (Owner IA).
  const [drawerTab, setDrawerTab] = useState<AtomicTabId | null>(null);
  const [drawerPinned, setDrawerPinned] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(DRAWER_PINNED_KEY) === '1';
  });
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });
  useBookmarkSignals(agentId);

  const handleBookmarkOpen = (tab: AtomicTabId) => {
    // Re-clicking the open tab closes the drawer (toggle).
    if (drawerTab === tab) {
      setDrawerTab(null);
      return;
    }
    setDrawerTab(tab);
    try {
      window.localStorage.setItem(DRAWER_OPENED_ONCE_KEY, '1');
    } catch { /* storage unavailable — onboarding hint just stays */ }
  };

  const handleDrawerClose = () => setDrawerTab(null);

  const handlePinnedChange = (pinned: boolean) => {
    setDrawerPinned(pinned);
    try {
      window.localStorage.setItem(DRAWER_PINNED_KEY, pinned ? '1' : '0');
    } catch { /* non-fatal */ }
  };

  const loadPinned = useArtifactStore((s) => s.loadPinned);
  const artifactsLength = useArtifactStore((s) => s.artifacts.length);
  const artifactsCollapsed = useArtifactStore((s) => s.collapsed);

  // Chat ↔ Artifacts split (fraction of joint width occupied by chat).
  //
  // Perf: dragging the divider does NOT resize the panes in real time —
  // resizing the artifact pane reflows whatever it hosts (an HTML
  // artifact is a sandboxed iframe; reflowing it every frame, especially
  // shrinking, is visibly janky). Instead, during the drag only a thin
  // "ghost" preview line moves (positioned imperatively, zero React
  // renders). On release `handleResizeEnd` commits the final ratio to
  // `chatSplit` state — exactly one re-render → one reflow per drag.
  const [chatSplit, setChatSplit] = useState<number>(() => readInitialSplit());
  const groupRef = useRef<HTMLDivElement | null>(null);
  const ghostLineRef = useRef<HTMLDivElement | null>(null);
  const pendingSplitRef = useRef<number>(chatSplit);

  // Persist on every committed change so refreshes preserve the layout.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(SPLIT_STORAGE_KEY, String(chatSplit));
  }, [chatSplit]);

  // Translate a raw pointer clientX into a clamped split fraction relative
  // to the joint chat+artifact area. Clamp tight against per-pane min
  // widths so neither pane can be dragged below its declared minimum.
  const computeSplit = useCallback((clientX: number): number | null => {
    const el = groupRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const rawRatio = (clientX - rect.left) / rect.width;
    const minRatio = Math.max(SPLIT_HARD_MIN, MIN_CHAT_PX / rect.width);
    const maxRatio = Math.min(SPLIT_HARD_MAX, 1 - MIN_ARTIFACT_PX / rect.width);
    // If the container is too narrow for both mins, just clamp to hard bounds.
    const lo = Math.min(minRatio, maxRatio);
    const hi = Math.max(minRatio, maxRatio);
    return Math.min(hi, Math.max(lo, rawRatio));
  }, []);

  // During drag (≤ once per frame): move the ghost preview line only. The
  // real columns stay put — no flex change, no iframe reflow.
  const handleResize = useCallback((clientX: number) => {
    const el = groupRef.current;
    const ghost = ghostLineRef.current;
    if (!el || !ghost) return;
    const split = computeSplit(clientX);
    if (split === null) return;
    pendingSplitRef.current = split;
    // Position the ghost at the *clamped* split so it previews exactly
    // where the panes will snap to on release.
    ghost.style.left = `${split * el.getBoundingClientRect().width}px`;
    ghost.style.display = 'block';
  }, [computeSplit]);

  // On release: hide the ghost, commit the final ratio to state (one
  // re-render → the columns resize and their content reflows once).
  const handleResizeEnd = useCallback((clientX: number) => {
    if (ghostLineRef.current) ghostLineRef.current.style.display = 'none';
    const split = computeSplit(clientX);
    if (split !== null) pendingSplitRef.current = split;
    setChatSplit(pendingSplitRef.current);
  }, [computeSplit]);

  // Load pinned artifacts whenever agentId changes.
  // Note: chatStore does not expose a per-agent session ID, so loadForSession
  // is not called here. Session-scoped artifacts arrive via the chat WS stream
  // (tool_output frames parsed in ChatPanel.tsx).
  // TODO: if chatStore gains a sessionId field, add loadForSession(agentId, sessionId) here.
  useEffect(() => {
    if (!agentId) return;
    loadPinned(agentId);
  }, [agentId, loadPinned]);

  // The divider only makes sense when the artifact column is in expanded
  // mode (has artifacts AND is not collapsed). In sliver mode the artifact
  // pane is a fixed 36-px button and a resize handle next to it would just
  // confuse the user.
  const artifactExpanded = !!agentId && artifactsLength > 0 && !artifactsCollapsed;

  return (
    <main className="flex-1 flex min-w-0 p-5 gap-5 overflow-hidden relative z-10">
      {/* Chat + Artifact group — owns the resizable divider + ghost line.
          `relative` so the ghost preview line can absolutely-position
          itself against this box. */}
      <div
        ref={groupRef}
        className="relative flex-1 min-w-0 flex overflow-hidden"
      >
        {/* Chat column — NM paper card (the actual conversation surface,
            --nm-card sits on top of the warm nm-paper background).
            flex-col so the (cloud-only, self-hiding) onboarding checklist
            can sit above the chat without ChatPanel losing its height. */}
        <div
          className="min-w-[400px] animate-fade-in overflow-hidden rounded-[var(--radius-md)] flex flex-col"
          style={{
            background: 'var(--nm-card)',
            border: '1px solid var(--nm-hairline)',
            ...(artifactExpanded
              ? { flexGrow: chatSplit, flexBasis: 0 }
              : { flexGrow: 1, flexBasis: 0 }),
          }}
        >
          <OnboardingChecklist />
          <div className="relative flex-1 min-h-0">
            <ChatPanel onAgentComplete={refreshAll} />
          </div>
        </div>

        {/* Resizable divider (chat ↔ artifacts). Hidden in sliver mode.
            handleResize only moves the ghost line; handleResizeEnd
            commits the split to state once on release. */}
        {artifactExpanded && (
          <ResizableDivider onResize={handleResize} onResizeEnd={handleResizeEnd} />
        )}

        {/* Artifact column — auto-hides when no artifacts are loaded.
            flexGrow is passed only when the column is actually expanded so
            the sliver path keeps its fixed 36-px width. */}
        {agentId && (
          <ArtifactColumn
            agentId={agentId}
            flexGrow={artifactExpanded ? 1 - chatSplit : undefined}
          />
        )}

        {/* Drag preview line — only exists alongside the divider. Hidden
            (display:none) until handleResize toggles it on; positioned
            imperatively so the drag never triggers a React render. */}
        {artifactExpanded && (
          <div
            ref={ghostLineRef}
            className="absolute top-0 bottom-0 w-0.5 bg-[var(--text-primary)] pointer-events-none z-20 hidden"
            aria-hidden
          />
        )}
      </div>

      {/* Pinned drawer — a static paper-warm column, only when the user
          explicitly pinned the bookmark drawer. The default experience is
          the slide-over (rendered below via portal) so chat keeps the
          space (spec §6). */}
      {drawerPinned && drawerTab && agentId && (
        <div
          className="w-[400px] shrink-0 flex flex-col rounded-[var(--radius-md)] overflow-hidden"
          style={{
            background: 'var(--nm-paper-warm)',
            border: '1px solid var(--nm-hairline)',
          }}
        >
          <BookmarkDrawer
            open
            pinned
            onPinnedChange={handlePinnedChange}
            onClose={handleDrawerClose}
            title={tabLabel(drawerTab)}
          >
            <BookmarkPanelHost tab={drawerTab} agentId={agentId} />
          </BookmarkDrawer>
        </div>
      )}

      {/* Bookmark strip — the paper edge. ~36px, always present; replaces
          the permanent 5-tab context column (spec §2). */}
      {agentId && (
        <BookmarkStrip
          agentId={agentId}
          activeTab={drawerTab}
          onOpen={handleBookmarkOpen}
        />
      )}

      {/* Hand-annotated page guide — bottom-left ?, spec §12 */}
      <HelpButton pages={CHAT_VIEW_PAGES} />

      {/* Slide-over drawer (default, unpinned) */}
      {!drawerPinned && agentId && (
        <BookmarkDrawer
          open={drawerTab !== null}
          pinned={false}
          onPinnedChange={handlePinnedChange}
          onClose={handleDrawerClose}
          title={drawerTab ? tabLabel(drawerTab) : ''}
        >
          {drawerTab && <BookmarkPanelHost tab={drawerTab} agentId={agentId} />}
        </BookmarkDrawer>
      )}
    </main>
  );
}

export function MainLayout() {
  const { agentId, userId } = useConfigStore();
  const { preloadAll } = usePreloadStore();
  const location = useLocation();

  // Check if we are rendering a sub-page (system, settings) vs. the chat view
  const isSubPage = location.pathname !== '/app/chat' && location.pathname !== '/app';

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  return (
    <div className="h-screen flex bg-[var(--bg-deep)] relative overflow-hidden">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Background agent completion toasts */}
      <AgentCompletionToast />

      {/* Render sub-page via Outlet, or the default chat view */}
      {isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          {/* v2.2 G1: inner Suspense so lazy sub-pages (DashboardPage etc.)
              don't trigger the App-level full-screen spinner that hides the
              Sidebar. The skeleton mirrors the dashboard grid shape. */}
          <Suspense fallback={<DashboardSkeleton />}>
            <Outlet />
          </Suspense>
        </main>
      ) : (
        <ChatView />
      )}
    </div>
  );
}

export default MainLayout;
