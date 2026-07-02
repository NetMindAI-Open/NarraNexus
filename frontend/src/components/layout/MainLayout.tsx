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
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ResizableDivider } from './ResizableDivider';
import {
  BookmarkStrip,
  BookmarkDrawer,
  BookmarkPanelHost,
  tabLabelKey,
} from '@/components/bookmarks';
import type { AtomicTabId } from '@/components/bookmarks';
import { HelpButton, CHAT_VIEW_PAGES } from '@/components/help';
import { useBookmarkSignals } from '@/hooks/useBookmarkSignals';
import { ChatPanel } from '@/components/chat';
import { WakingOverlay } from '@/components/chat/WakingOverlay';
import { TeamChatPanel } from '@/components/chat/TeamChatPanel';
import { CostPopover } from '@/components/cost/CostPopover';
import { OnboardingChecklist } from '@/components/onboarding/OnboardingChecklist';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { ArtifactColumn } from '@/components/artifacts';
import { useConfigStore, usePreloadStore, useArtifactStore, useUIStore } from '@/stores';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { cn } from '@/lib/utils';
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
  const { t: tr } = useTranslation();
  const [drawerTab, setDrawerTab] = useState<AtomicTabId | null>(null);
  const [drawerPinned, setDrawerPinned] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(DRAWER_PINNED_KEY) === '1';
  });
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });
  useBookmarkSignals(agentId);

  // Mobile: the chat ↔ artifacts split collapses to a tab switcher, and the
  // right bookmark strip hides (panels reached via the ⌘K palette instead).
  const isMobile = useIsMobile();
  const [mobileTab, setMobileTab] = useState<'chat' | 'artifacts'>('chat');
  const pendingPanel = useUIStore((s) => s.pendingPanel);
  const clearPendingPanel = useUIStore((s) => s.clearPendingPanel);

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

  // A panel requested from the command palette (mobile entry point) — open its
  // drawer, then clear the request so it fires once.
  useEffect(() => {
    if (pendingPanel) {
      setDrawerTab(pendingPanel as AtomicTabId);
      clearPendingPanel();
    }
  }, [pendingPanel, clearPendingPanel]);

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
    <main className="flex-1 flex min-w-0 p-2 gap-2 md:p-3 md:gap-3 overflow-hidden relative z-10">
      {/* Chat + Artifact group — owns the resizable divider + ghost line.
          `relative` so the ghost preview line can absolutely-position
          itself against this box. */}
      <div
        ref={groupRef}
        className="relative flex-1 min-w-0 flex flex-col md:flex-row overflow-hidden"
      >
        {/* Mobile: centered Chat / Artifacts tab switcher with the activity
            (heartbeat) icon docked on the right. The side artifact column
            doesn't fit on phones, so the two views share the pane; this row
            also stands in for the chat header (hidden on mobile). The left
            spacer balances the right icon so the tabs stay centered. */}
        {isMobile && agentId && (
          <div className="flex h-9 shrink-0 items-center border-b border-[var(--nm-hairline)]">
            <div className="flex-1" />
            <div className="flex h-full items-stretch justify-center gap-6" role="tablist">
              {(['chat', 'artifacts'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="tab"
                  aria-selected={mobileTab === t}
                  onClick={() => setMobileTab(t)}
                  className={cn(
                    'px-2 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.12em] transition-colors border-b-2',
                    mobileTab === t
                      ? 'text-[var(--color-carbon)] border-[var(--color-carbon)]'
                      : 'text-[var(--text-tertiary)] border-transparent',
                  )}
                >
                  {t === 'chat'
                    ? tr('layout.chatView.tabChat')
                    : artifactsLength > 0
                      ? tr('layout.chatView.tabArtifactsCount', { count: artifactsLength })
                      : tr('layout.chatView.tabArtifacts')}
                </button>
              ))}
            </div>
            <div className="flex flex-1 justify-end pr-1.5">
              <CostPopover compact />
            </div>
          </div>
        )}

        {/* Chat column — NM paper card (the actual conversation surface,
            --nm-card sits on top of the warm nm-paper background).
            flex-col so the (cloud-only, self-hiding) onboarding checklist
            can sit above the chat without ChatPanel losing its height. */}
        <div
          className={cn(
            'min-w-0 lg:min-w-[400px] animate-fade-in overflow-hidden rounded-[var(--radius-md)] flex flex-col',
            isMobile && mobileTab === 'artifacts' && 'hidden',
          )}
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
            <WakingOverlay />
          </div>
        </div>

        {/* Resizable divider (chat ↔ artifacts) — desktop only. Hidden in
            sliver mode; on mobile the views are tabbed, not split. */}
        {!isMobile && artifactExpanded && (
          <ResizableDivider onResize={handleResize} onResizeEnd={handleResizeEnd} />
        )}

        {/* Artifact column. Desktop: side column (auto-hides to a sliver when
            empty; flexGrow drives the split). Mobile: only rendered when the
            Artifacts tab is active, forced to the full expanded view. */}
        {agentId &&
          (isMobile
            ? mobileTab === 'artifacts' && (
                <ArtifactColumn agentId={agentId} forceExpanded />
              )
            : (
                <ArtifactColumn
                  agentId={agentId}
                  flexGrow={artifactExpanded ? 1 - chatSplit : undefined}
                />
              ))}

        {/* Drag preview line — desktop only, alongside the divider. */}
        {!isMobile && artifactExpanded && (
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
            title={tr(tabLabelKey(drawerTab))}
          >
            <BookmarkPanelHost tab={drawerTab} agentId={agentId} />
          </BookmarkDrawer>
        </div>
      )}

      {/* Bookmark strip — the paper edge. ~36px (spec §2). Desktop only; on
          mobile the panels are reached from the ⌘K command palette instead,
          which sets pendingPanel → opens the same drawer. */}
      {!isMobile && agentId && (
        <BookmarkStrip
          agentId={agentId}
          activeTab={drawerTab}
          onOpen={handleBookmarkOpen}
        />
      )}

      {/* Hand-annotated page guide — bottom-left ?, spec §12 */}
      {/* Floating help (?) — desktop only; on mobile the bottom-right corner
          is reserved for content and the page guide isn't tuned for touch. */}
      {!isMobile && <HelpButton pages={CHAT_VIEW_PAGES} />}

      {/* Slide-over drawer (default, unpinned) */}
      {!drawerPinned && agentId && (
        <BookmarkDrawer
          open={drawerTab !== null}
          pinned={false}
          onPinnedChange={handlePinnedChange}
          onClose={handleDrawerClose}
          title={drawerTab ? tr(tabLabelKey(drawerTab)) : ''}
        >
          {drawerTab && <BookmarkPanelHost tab={drawerTab} agentId={agentId} />}
        </BookmarkDrawer>
      )}
    </main>
  );
}

/**
 * Team group-chat view. Occupies the same main slot as ChatView so
 * switching between a single agent and a team feels seamless (no
 * sub-page overlay / close-X). Artifacts + bookmarks are intentionally
 * omitted for now — the shared room is the focus.
 */
export function TeamChatView({ teamId }: { teamId: string }) {
  return (
    <main className="flex-1 flex min-w-0 p-2 gap-2 md:p-3 md:gap-3 overflow-hidden relative z-10">
      <div
        className="flex-1 min-w-0 animate-fade-in overflow-hidden rounded-[var(--radius-md)] flex flex-col"
        style={{ background: 'var(--nm-card)', border: '1px solid var(--nm-hairline)' }}
      >
        <TeamChatPanel teamId={teamId} />
      </div>
    </main>
  );
}

export function MainLayout() {
  const { t } = useTranslation();
  const { agentId, userId } = useConfigStore();
  const { preloadAll } = usePreloadStore();
  const location = useLocation();
  const navigate = useNavigate();
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);

  // Close the mobile sidebar drawer whenever the view changes (picked an agent
  // or navigated to a sub-page) so the user lands on the content they tapped.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, agentId, setMobileNavOpen]);

  // Team group chat (`/app/teams/:teamId/chat`) renders in the main slot like
  // the chat view — a seamless switch between a single agent and a team, NOT a
  // sub-page overlay with a close-X.
  const teamChatMatch = location.pathname.match(/^\/app\/teams\/([^/]+)\/chat$/);
  const teamChatId = teamChatMatch ? teamChatMatch[1] : null;

  // Check if we are rendering a sub-page (system, settings) vs. the chat view
  const isSubPage =
    !teamChatId &&
    location.pathname !== '/app/chat' &&
    location.pathname !== '/app';

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  return (
    <div className="h-screen flex flex-col bg-[var(--bg-deep)] relative overflow-hidden">
      {/* Narrow global status strip — breadcrumb + connection + ⌘K */}
      <TopBar />

      <div className="flex flex-1 min-h-0 relative">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Mobile drawer backdrop — taps to close the off-canvas sidebar.
          Sits below the sidebar (z-40) and above the content (z-10). */}
      {mobileNavOpen && (
        <div
          className="fixed inset-x-0 bottom-0 top-9 z-30 bg-[var(--nm-backdrop)] md:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden
        />
      )}

      {/* Background agent completion toasts */}
      <AgentCompletionToast />

      {/* Render: team group chat, a sub-page via Outlet, or the chat view */}
      {teamChatId ? (
        <TeamChatView teamId={teamChatId} />
      ) : isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          {/* Close button — sub-pages (Dashboard / Settings / System …) open
              over the chat with no obvious way back, so dock an X top-right
              that returns to the conversation. */}
          <button
            type="button"
            onClick={() => navigate('/app/chat')}
            title={t('layout.subPage.closeTitle')}
            aria-label={t('layout.subPage.closeAriaLabel')}
            className="absolute top-4 right-4 z-30 flex h-6 w-6 items-center justify-center rounded-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
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
    </div>
  );
}

export default MainLayout;
