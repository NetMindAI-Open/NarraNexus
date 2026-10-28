/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure (Chat UI v4):
 * ┌──────────┬──────────────────────────────────┬──────────────────┐
 * │  Sidebar │            Chat Area             │ Bookmark Drawer  │
 * │  (Agent  │  (full-bleed, header owns all    │ (pinned column / │
 * │   List)  │   panel + artifacts entries)     │  slide-over)     │
 * └──────────┴──────────────────────────────────┴──────────────────┘
 *
 * Panel entries (Awareness / Jobs / Inbox / Artifacts / …) live in the chat
 * header (v4); they funnel through uiStore.requestPanel into the single
 * BookmarkDrawer below. The old right-edge BookmarkStrip AND the resizable
 * side Artifact Column are retired — artifacts render as a drawer panel
 * like everything else (Owner 2026-08-06). The pinned drawer keeps its own
 * ResizableDivider + persisted width.
 *
 * Signal source: artifact_id signals arrive via the chat WebSocket stream
 * (tool_output frames parsed in ChatPanel.tsx). loadPinned is called on mount /
 * agent change to hydrate agent-scoped artifacts (feeds the header badge and
 * the drawer panel). No dedicated artifact WS.
 */

import { useState, useEffect, Suspense } from 'react';
import { initReplyLanguageSync } from '@/lib/replyLanguageSync';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { X, MonitorPlay, PanelLeft } from 'lucide-react';
import { Button } from '@/components/nm/button';
import { PANELS, useRegistryEntries } from '@/platform/registries';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { CommandPalette } from './CommandPalette';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ResizableDivider } from './ResizableDivider';
import {
  BookmarkDrawer,
  BookmarkPanelHost,
  tabLabelKey,
  tabDescKey,
  visibleCategories,
} from '@/components/bookmarks';
import type { AtomicTabId } from '@/components/bookmarks';
import { HelpButton, CHAT_VIEW_PAGES } from '@/components/help';
import { FeedbackButton } from '@/components/ui/FeedbackButton';
import { TelemetryNotice } from '@/components/telemetry/TelemetryNotice';
import { WebAnalyticsNotice } from '@/components/analytics/WebAnalyticsNotice';
import { useBookmarkSignals } from '@/hooks/useBookmarkSignals';
import { useStudioLifecycle } from '@/hooks/useStudioLifecycle';
import { ChatPanel } from '@/components/chat';
import { WakingOverlay } from '@/components/chat/WakingOverlay';
import { TeamChatPanel } from '@/components/chat/team';
import { CostPopover } from '@/components/cost/CostPopover';
import { GuideAgentCoachmark } from '@/components/onboarding/GuideAgentCoachmark';
import { MigrationGuide } from '@/components/onboarding/MigrationGuide';
import { NoProviderNotice } from './NoProviderNotice';
import { BrowserNotices } from './BrowserNotices';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { useConfigStore, usePreloadStore, useArtifactStore, useUIStore } from '@/stores';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { useAutoRefresh } from '@/hooks';
import { DrawerCoachMark } from '@/components/bookmarks/DrawerCoachMark';
import {
  DRAWER_OPENED_ONCE_KEY,
  markAgentDrawerSeen,
  markFirstRunSeen,
  shouldAutoOpenFirstRun,
  shouldAutoOpenForAgent,
} from './drawerLayout';
import { usePinnedDrawer } from '@/hooks/usePinnedDrawer';

// Pinned bookmark drawer: user-resizable like the chat ↔ artifacts split, so
// every column on the right side follows the same "grab the rule and drag"
// rule instead of one of them being a hardcoded width. Sizing policy lives
// in drawerLayout.ts; the React wiring is the shared usePinnedDrawer hook —
// the team room mounts the SAME drawer with the same preferences.

/** Default chat view with context panel */
export function ChatView() {
  // Bookmark drawer: which atomic tab is open (null = closed) and whether
  // the drawer is pinned into a static column (persisted — pinning is a
  // deliberate workspace choice). One tab = one panel (Owner IA).
  const { t: tr } = useTranslation();
  // First run (desktop): the artifacts panel opens pinned with a coach mark
  // teaching unpin/close. The decision is a read-only lazy initializer (no
  // cascading render); the seen-marker is written from the mount effect
  // below, so a discarded render pass cannot burn it.
  const [showDrawerCoach, setShowDrawerCoach] = useState<boolean>(() =>
    typeof window !== 'undefined' &&
    shouldAutoOpenFirstRun(window.localStorage, window.matchMedia('(max-width: 767.98px)').matches),
  );
  useEffect(() => {
    if (showDrawerCoach) markFirstRunSeen(window.localStorage);
    // First-run decision is mount-time only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const { agentId, userId } = useConfigStore();
  // Per-agent first view (Owner 2026-09-11): an agent whose chat was never
  // shown on this desktop opens with the drawer on Artifacts — pinned, the
  // pin default — so a new agent's explainer is visible immediately, for new
  // AND existing users (the coach above only reaches brand-new profiles).
  // Decided at mount (lazy initializer) and on every agent switch (derived
  // state set during render — no effect → no flash of a closed drawer); the
  // seen-marker is written from the effect below.
  const isSmallViewport = () =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767.98px)').matches;
  const autoOpenFor = (id: string | null | undefined) =>
    typeof window !== 'undefined' && shouldAutoOpenForAgent(window.localStorage, id, isSmallViewport());
  const [drawerTab, setDrawerTab] = useState<AtomicTabId | null>(
    () => (showDrawerCoach || autoOpenFor(agentId) ? 'artifacts' : null),
  );
  const [drawerAgentId, setDrawerAgentId] = useState(agentId);
  if (agentId !== drawerAgentId) {
    setDrawerAgentId(agentId);
    if (autoOpenFor(agentId)) setDrawerTab('artifacts');
  }
  useEffect(() => {
    // A phone visit does not spend the agent's desktop first view.
    if (agentId && !isSmallViewport()) markAgentDrawerSeen(window.localStorage, agentId);
  }, [agentId]);
  const {
    pinned: drawerPinned,
    setPinned: setDrawerPinned,
    effectiveWidth: effectiveDrawerWidth,
    colRef: drawerColRef,
    handleResize: handleDrawerResize,
    handleResizeEnd: handleDrawerResizeEnd,
  } = usePinnedDrawer();
  const { refreshAll } = useAutoRefresh({ agentId, userId });
  useBookmarkSignals(agentId);

  const isMobile = useIsMobile();
  const browserOffered = useRegistryEntries(PANELS).some((entry) => entry.id === 'browser');
  const pendingPanel = useUIStore((s) => s.pendingPanel);
  const pendingPanelMode = useUIStore((s) => s.pendingPanelMode);
  const clearPendingPanel = useUIStore((s) => s.clearPendingPanel);
  const requestPanel = useUIStore((s) => s.requestPanel);
  const openPanel = useUIStore((s) => s.openPanel);

  // The creation studio lives exactly as long as its panel is what this
  // drawer shows for this agent — reconciled in ONE place (useStudioLifecycle),
  // not at each way the drawer can close. Which entries OFFER its tab is the
  // registry's business (bookmarks/tabs `conditional: 'studio'`), consumed by
  // the chat header's ⋯ menu, the ⌘K palette and the drawer's title switcher
  // below (via visibleCategories, the same rule grouped by category).
  const { studioOpen, studioResumable } = useStudioLifecycle({ agentId, drawerTab, setDrawerTab });
  // Computed per render, not memoized: PANELS can gain plugin panels after
  // mount, and the derivation is a sort over ~a dozen entries.
  const switcherCategories = visibleCategories({ studioOpen, studioResumable });

  const handleDrawerClose = () => {
    setDrawerTab(null);
    setShowDrawerCoach(false);
  };

  // A panel requested from the chat header entries / ⋯ detail menu / the
  // command palette / the drawer's title switcher — all funnel through
  // uiStore (requestPanel / openPanel).
  // Re-requesting the open tab closes the drawer (toggle).
  useEffect(() => {
    if (pendingPanel) {
      setDrawerTab((prev) => {
        // 'open' callers must end up open; only a user toggle closes.
        if (pendingPanelMode === 'toggle' && prev === (pendingPanel as AtomicTabId)) return null;
        try {
          window.localStorage.setItem(DRAWER_OPENED_ONCE_KEY, '1');
        } catch { /* storage unavailable — onboarding hint just stays */ }
        return pendingPanel as AtomicTabId;
      });
      clearPendingPanel();
    }
  }, [pendingPanel, pendingPanelMode, clearPendingPanel]);

  const handlePinnedChange = (pinned: boolean) => {
    setDrawerPinned(pinned);
    setShowDrawerCoach(false);
  };


  const loadPinned = useArtifactStore((s) => s.loadPinned);
  const artifactsLength = useArtifactStore((s) => s.artifacts.length);


  // Load pinned artifacts whenever agentId changes — even with the side
  // column retired, the header badge count and the drawer panel both read
  // from this hydration.
  // Note: chatStore does not expose a per-agent session ID, so loadForSession
  // is not called here. Session-scoped artifacts arrive via the chat WS stream
  // (tool_output frames parsed in ChatPanel.tsx).
  // TODO: if chatStore gains a sessionId field, add loadForSession(agentId, sessionId) here.
  useEffect(() => {
    if (!agentId) return;
    loadPinned(agentId);
  }, [agentId, loadPinned]);

  return (
    // Full-bleed, seam-free — the chat surface runs edge to edge with
    // hairline separations instead of padded floating cards. Artifacts do
    // not occupy a side column: they open in the bookmark drawer via the
    // chat header's Artifacts entry.
    <main className="flex-1 flex min-w-0 overflow-hidden relative z-10">
      <div className="relative flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* The desktop header is hidden on mobile; keep its activity panels
            reachable here even before the agent has produced an artifact. */}
        {isMobile && agentId && (
          <div className="flex h-9 shrink-0 items-center justify-end gap-1 px-1.5 border-b border-[var(--nm-hairline)]">
            <button
              type="button"
              onClick={() => requestPanel('artifacts')}
              className="inline-flex items-center gap-1.5 px-2 h-7 rounded-[var(--radius-sm)] font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] transition-colors hover:text-[var(--nm-ink)]"
            >
              {artifactsLength > 0
                ? tr('layout.chatView.tabArtifactsCount', { count: artifactsLength })
                : tr('layout.chatView.tabArtifacts')}
            </button>
            {browserOffered && (
              <Button variant="ghost" size="sm" onClick={() => requestPanel('browser')}
                leading={<MonitorPlay className="h-3.5 w-3.5" />}
                className="h-7 px-2 text-[11px] text-[var(--text-tertiary)]">
                {tr('rail.browser', 'Browser')}
              </Button>
            )}
            <CostPopover compact />
          </div>
        )}

        {/* Chat column — NM paper card (the actual conversation surface,
            --nm-card sits on top of the warm nm-paper background). */}
        <div
          className="min-w-0 flex-1 animate-fade-in overflow-hidden flex flex-col"
          style={{ background: 'var(--nm-card)' }}
        >
          {/* One-shot new-user nudge (portals to body; renders nothing once
              dismissed). Replaces the retired OnboardingChecklist card — the
              auto-provisioned guide agent now carries the onboarding. */}
          <GuideAgentCoachmark />
          <MigrationGuide />
          <div className="relative flex-1 min-h-0">
            <ChatPanel onAgentComplete={refreshAll} />
            <WakingOverlay />
          </div>
        </div>
      </div>

      {/* Drag handle for the pinned drawer's width. */}
      {drawerPinned && drawerTab && agentId && !isMobile && (
        <ResizableDivider
          onResize={handleDrawerResize}
          onResizeEnd={handleDrawerResizeEnd}
          label={tr('layout.resizableDivider.drawerAriaLabel')}
          title={tr('layout.resizableDivider.drawerTitle')}
        />
      )}

      {/* The bookmark drawer — ONE element for both modes, deliberately.
          Pinned it renders here as a static column; unpinned it portals out to
          body as a slide-over. Keeping it a single element at a single position
          in the React tree is what stops a pin/unpin toggle from unmounting the
          panel and silently discarding everything the user had set up inside it
          (filters, view mode, expanded rows). Do NOT split this back into two
          conditional <BookmarkDrawer> elements. */}
      {agentId && (
        <BookmarkDrawer
          open={drawerTab !== null}
          pinned={drawerPinned && !isMobile}
          onPinnedChange={handlePinnedChange}
          onClose={handleDrawerClose}
          title={drawerTab ? tr(tabLabelKey(drawerTab)) : ''}
          description={drawerTab ? tr(tabDescKey(drawerTab), '') : ''}
          edgeReservePx={0}
          pinnedWidth={effectiveDrawerWidth}
          // Title switcher (Owner-required): a pinned drawer switches its own
          // content without a trip back to the chat header. It goes through
          // the same uiStore funnel as every other panel entry ('open' mode:
          // the switcher never re-selects the open tab, so no toggle).
          activeTab={drawerTab}
          onSelectTab={openPanel}
          switcherCategories={switcherCategories}
          banner={
            showDrawerCoach ? (
              <DrawerCoachMark onDismiss={() => setShowDrawerCoach(false)} />
            ) : null
          }
          // Desktop: transient drawer is an in-flow column (chat shifts left,
          // nothing gets covered). Artifacts wants big-screen readability —
          // ~half the viewport, clamped so the sidebar (272) + chat (400)
          // always keep their minimum room; other panels stay at 440px.
          inset={!isMobile}
          insetWidth={
            drawerTab === 'artifacts'
              ? 'min(max(440px, 50vw), max(320px, calc(100vw - 672px)))'
              : 'min(440px, max(320px, calc(100vw - 672px)))'
          }
          columnRef={drawerColRef}
        >
          {drawerTab && <BookmarkPanelHost tab={drawerTab} agentId={agentId} />}
        </BookmarkDrawer>
      )}

      {/* The right-edge bookmark strip is retired in v4 — every panel entry
          now lives in the chat header (icons + ⋯ detail menu), all funneling
          through uiStore.requestPanel into the same drawer above. */}

      {/* Hand-annotated page guide — bottom-left ?, spec §12 */}
      {/* Floating help (?) — desktop only; on mobile the bottom-right corner
          is reserved for content and the page guide isn't tuned for touch. */}
      {!isMobile && <HelpButton pages={CHAT_VIEW_PAGES} />}

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
    <main className="flex-1 flex min-w-0 overflow-hidden relative z-10">
      <div
        className="flex-1 min-w-0 animate-fade-in overflow-hidden flex flex-col"
        style={{ background: 'var(--nm-card)' }}
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
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const paletteOpen = useUIStore((s) => s.paletteOpen);
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen);
  const isMobile = useIsMobile();

  // Global ⌘K / Ctrl+K — hosted here (not the mobile-only TopBar) so the
  // palette works on every viewport and route.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        const { paletteOpen: open, setPaletteOpen: setOpen } = useUIStore.getState();
        setOpen(!open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

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

  // Sub-pages that already carry their own way back. The agent profile has a
  // source-aware breadcrumb (back to Chat or back to Agents, per
  // location.state.from) top-left AND a Chat + "⋮" action pair top-right; the
  // generic X would both duplicate the former and sit on top of the latter.
  const hasOwnCloseControl = location.pathname.startsWith('/app/agents/');

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  // Reply-language sync: languageChanged subscription + one-time backfill.
  // Mounted HERE (MainLayout, like TelemetryNotice/FeedbackButton), NOT in
  // ChatView: a team-first user or a settings deep-link never renders
  // ChatView, and a sync only chat users get is not a sync (PR #284 r2).
  useEffect(() => {
    initReplyLanguageSync(userId);
  }, [userId]);

  return (
    // h-dvh-safe (not h-screen): 100vh on mobile includes the space behind
    // the browser's retractable UI, pushing the layout's bottom edge under
    // the toolbar. The class carries a plain-vh fallback for engines
    // without dvh (see index.css).
    <div className="h-dvh-safe flex flex-col bg-[var(--bg-deep)] relative overflow-hidden">
      {/* "You skipped wiring a model" strip — above everything, because it
          explains why the app can't answer yet. Self-clears once a provider
          exists; local only. */}
      <NoProviderNotice />

      {/* A site the agent is blocked on. In the shell, not in the browser
          panel: the user reads the refusal in chat, and a prompt that only
          exists inside a panel they have not opened is no prompt at all. */}
      <BrowserNotices />

      {/* Mobile-only status strip — hamburger + breadcrumb + ⌘K. Renders
          nothing on md+ (v4: the sidebar owns the full height there). */}
      <TopBar />

      {/* Command palette — one instance for all viewports and routes. */}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      <div className="flex flex-1 min-h-0 relative">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Collapsed-sidebar expand rail. The chat view renders its own inline
          expand button in the chat header (v4); sub-pages and team chat get
          a slim reserved rail instead of a floating chip — the page content
          shifts right, so nothing is ever covered (Owner 2026-08-06). */}
      {!isMobile && sidebarCollapsed && (isSubPage || teamChatId) && (
        <div className="shrink-0 flex w-11 flex-col items-center border-r border-[var(--nm-hairline)] bg-[var(--nm-paper)] pt-3">
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            title={t('sidebar.expandTitle')}
            aria-label={t('sidebar.expandTitle')}
            className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink50)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        </div>
      )}

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

      {/* Feedback entry — every desktop route, not just the chat view (the
          sidebar-footer entry it replaced was global too). It occupies the
          corner slot when there's no "?" (sub-pages) and stacks above it on
          the chat view. Mobile keeps its entry in the sidebar drawer footer:
          the corner belongs to the composer there. */}
      {!isMobile && <FeedbackButton aboveHelp={!isSubPage && !teamChatId} />}
      {/* One-time privacy disclosures. Mounted HERE (MainLayout, like
          FeedbackButton) and not inside ChatView: a team-first user or a
          settings deep-link never renders ChatView, and a disclosure that only
          chat users receive is not a disclosure. Both self-gate (render nothing
          once seen / when their data flow is off), so this shared stacking slot
          sizes itself and the two never overlap — pointer-events-none on the
          slot so it never blocks the composer when both are hidden. */}
      <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[calc(100%-2rem)] max-w-md flex flex-col gap-3 pointer-events-none">
        <WebAnalyticsNotice />
        <TelemetryNotice />
      </div>

      {/* Render: team group chat, a sub-page via Outlet, or the chat view */}
      {teamChatId ? (
        <TeamChatView teamId={teamChatId} />
      ) : isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          {/* Close button — sub-pages (Dashboard / Settings / System …) open
              over the chat with no obvious way back, so dock an X top-right
              that returns to the conversation. Skipped on pages that bring
              their own back control (hasOwnCloseControl). */}
          {!hasOwnCloseControl && (
            <button
              type="button"
              onClick={() => navigate('/app/chat')}
              title={t('layout.subPage.closeTitle')}
              aria-label={t('layout.subPage.closeAriaLabel')}
              className="absolute top-4 right-4 z-30 flex h-6 w-6 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
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
