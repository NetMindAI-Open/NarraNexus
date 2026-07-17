/**
 * Sidebar - Bioluminescent Terminal style
 * Agent selection and navigation with dramatic visual effects
 */

import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useIsMobile } from '@/hooks/useMediaQuery';
import {
  LogOut,
  ChevronLeft,
  ChevronRight,
  Sliders,
  Server,
  Monitor,
  Cloud,
  RotateCcw,
  LayoutDashboard,
  MessageSquarePlus,
} from 'lucide-react';
import { Button, ThemeToggle, LanguageToggle, ScrollArea, useConfirm } from '@/components/ui';
import { FeedbackDialog } from '@/components/ui/FeedbackDialog';
import { RingAvatar, StatusDot } from '@/components/nm';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/hooks';
import { useConfigStore, useChatStore, useRuntimeStore, usePreloadStore, useUIStore } from '@/stores';
import { cn } from '@/lib/utils';

// v2.2 G1: prefetch the lazy DashboardPage chunk on hover/focus so click
// arrives to a warm cache. Static literal -> Vite resolves at build time,
// no injection risk.
const prefetchDashboard = () => {
  void import('@/pages/DashboardPage');
};

// Left-nav hover/active treatment: light up the label + icon (carbon), no
// background fill — matches the right BookmarkStrip. Destructive actions
// (Clear History / Logout) light up in error red instead of carbon. Both
// override the ghost variant's default `hover:bg` via tailwind-merge.
const NAV_ITEM = 'text-[var(--text-secondary)] hover:bg-transparent hover:text-[var(--color-carbon)]';
const NAV_ITEM_ACTIVE = 'text-[var(--color-carbon)]';
const NAV_ITEM_DANGER = 'text-[var(--text-secondary)] hover:bg-transparent hover:text-[var(--color-error)]';
import { AgentList } from './AgentList';

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [showModePopup, setShowModePopup] = useState(false);
  // Mobile-only feedback entry. Desktop uses the floating FeedbackButton
  // (bottom-right, by the help "?"); on mobile that corner is the composer's,
  // so the drawer footer carries it instead. Exactly one entry per viewport.
  const [showFeedback, setShowFeedback] = useState(false);
  // Mobile (< md): the sidebar is an off-canvas drawer toggled from the TopBar.
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const isMobile = useIsMobile();

  // The icon-only collapsed layout makes no sense inside the mobile drawer
  // (it's a full-width overlay, not a docked rail) — force it expanded there.
  useEffect(() => {
    if (isMobile && collapsed) setCollapsed(false);
  }, [isMobile, collapsed]);
  const navigate = useNavigate();
  const location = useLocation();

  const { userId, displayName, logout } = useConfigStore();
  // user_id is an opaque NetMind userSystemCode (32-hex) in cloud mode, not
  // human-readable. Show the NetMind nickname when we have it; fall back to
  // user_id (local mode, where it IS the chosen username).
  const userLabel = displayName || userId;
  const { clearAll: clearChat } = useChatStore();
  const { mode, features, setMode, setCloudApiUrl } = useRuntimeStore();
  const clearPreload = usePreloadStore((s) => s.clearAll);

  // The cloud/local mode switcher is hidden from the sidebar — we don't want
  // users choosing the deployment mode. All the switching logic (handleSwitchMode,
  // mode state, /mode-select) is kept intact behind this flag so it can be
  // re-enabled by flipping to true; only the UI entry points are gated.
  const SHOW_MODE_SWITCHER = false;
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { isDark } = useTheme();
  const { t } = useTranslation();

  /**
   * Wipe all session + cached data before leaving the current mode.
   *
   * This is deliberately aggressive. We do NOT trust Zustand's persist
   * middleware to have flushed to localStorage by the time the subsequent
   * window.location.href reload happens — so we also manually
   * `removeItem()` every known persisted key. After the reload each store
   * will re-hydrate from whatever is (or is not) in localStorage, so
   * removed keys mean default-state stores.
   *
   * Keys wiped:
   *   - narra-nexus-config  → configStore (userId, token, agents, ...)
   *   - narranexus-runtime  → runtimeStore (mode, cloudApiUrl, ...)
   *   - lastSeenAwarenessTime:*  → written directly by configStore, not
   *                                 covered by any store's clearAll
   */
  const wipeAllSessionData = () => {
    // 1. Reset in-memory store state via each store's clearAll/logout.
    //    This updates the UI immediately and invokes persist middleware
    //    to sync localStorage (best-effort — we do not rely on it).
    logout();           // configStore
    clearChat();        // chatStore
    clearPreload();     // preloadStore

    // 2. Directly nuke every key in localStorage that could carry
    //    session state. This is the authoritative clear, independent
    //    of whatever Zustand persist may or may not have flushed yet.
    try {
      localStorage.removeItem('narra-nexus-config');
      localStorage.removeItem('narranexus-runtime');

      const auxKeys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('lastSeenAwarenessTime:')) {
          auxKeys.push(k);
        }
      }
      auxKeys.forEach((k) => localStorage.removeItem(k));
    } catch {
      // Safari private mode / other storage exceptions — ignore.
    }
  };

  const handleSwitchMode = () => {
    wipeAllSessionData();
    setCloudApiUrl('');
    setMode(null);
    setShowModePopup(false);

    // Hard reload, NOT React Router navigate. Soft navigation keeps the
    // React tree, closure-captured store snapshots, in-flight fetches,
    // and module-level caches from the previous mode alive — which is
    // exactly how cloud data was bleeding into a subsequent local
    // session. A full document reload tears everything down.
    //
    // Combined with the localStorage.removeItem() calls above, the next
    // page load starts from true factory defaults.
    window.location.href = '/mode-select';
  };

  const handleLogout = async () => {
    const ok = await confirm({
      title: t('layout.sidebar.logoutConfirmTitle'),
      message: t('layout.sidebar.logoutConfirmMessage'),
      confirmText: t('layout.sidebar.logoutConfirmAction'),
      danger: true,
    });
    if (!ok) return;
    wipeAllSessionData();
    window.location.href = '/login';
  };

  return (
    <aside
      className={cn(
        'flex flex-col relative',
        // NM canonical (FinChats:461): chat-list container bg = var(--nm-paper).
        // Rows sit on paper directly with rounded highlight when active.
        'bg-[color:var(--nm-paper)]',
        'border-r border-[color:var(--nm-hairline)]',
        'transition-all duration-300 ease-out',
        // Mobile (< md): off-canvas drawer below the 36px TopBar, slides in.
        // Height comes from top-9 + bottom-0 (NOT h-full, which would overflow
        // 36px below the viewport and clip the footer / theme toggle).
        'fixed top-9 bottom-0 left-0 z-40 w-72',
        mobileNavOpen ? 'translate-x-0 shadow-[var(--nm-elev-3)]' : '-translate-x-full',
        // Tablet/desktop (md+): back in normal flow, full height, width by collapse.
        'md:static md:top-auto md:bottom-auto md:z-auto md:h-full md:translate-x-0 md:shadow-none',
        collapsed ? 'md:w-[72px]' : 'md:w-72',
      )}
    >
      {confirmDialog}
      {isMobile && <FeedbackDialog isOpen={showFeedback} onClose={() => setShowFeedback(false)} />}

      {/* Header — original NarraNexus logo image preserved.
          Collapsed state hides the wordmark and keeps only the toggle button. */}
      <div className="p-4 border-b border-[var(--rule)]">
        <div className="flex items-center justify-between gap-2">
          {!collapsed && (
            <div className="flex items-center gap-0 animate-fade-in min-w-0">
              <img
                src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
                alt="NarraNexus"
                className="h-11 w-auto object-contain shrink-0"
              />
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setCollapsed(!collapsed)}
            className={cn('shrink-0 hidden md:inline-flex', collapsed && 'mx-auto')}
          >
            {collapsed ? (
              <ChevronRight className="w-4 h-4" />
            ) : (
              <ChevronLeft className="w-4 h-4" />
            )}
          </Button>
        </div>
      </div>

      {/* User Info — NM RingAvatar carbon (human species), name + StatusDot status row.
          Carbon ring marks "this is a human user" per Axiom #1. Clicking it opens
          the owner-scoped "You" workspace (Memory / Network / World + Notes) — the
          carbon counterpart to clicking an agent. */}
      {!collapsed && (
        <button
          type="button"
          onClick={() => navigate('/app/you')}
          aria-label={t('layout.sidebar.openWorkspace')}
          aria-current={location.pathname === '/app/you' ? 'page' : undefined}
          className={cn(
            'group w-full px-4 py-3 border-b border-[var(--rule)] text-left transition-colors',
            location.pathname === '/app/you'
              ? 'bg-[var(--bg-elevated)]'
              : 'hover:bg-[var(--bg-elevated)]',
          )}
        >
          <div className="flex items-center gap-3">
            <RingAvatar species="carbon" label={userLabel || '?'} size="sm" />
            <div className="flex-1 min-w-0 h-10 flex flex-col justify-center gap-1">
              <div className="text-[13px] leading-none text-[var(--text-primary)] truncate font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]" title={userLabel}>
                {userLabel}
              </div>
              <div className="flex items-center gap-1.5 text-[10px] leading-none text-[var(--text-tertiary)] uppercase tracking-[0.14em] font-[family-name:var(--font-mono)]">
                <StatusDot status="success" size={6} />
                <span>{t('sidebar.online')}</span>
              </div>
            </div>
            {/* Affordance: this row opens your "You" workspace — a chevron that
                brightens on hover (and a "your space" hint label). */}
            <span
              className={cn(
                'shrink-0 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] transition-colors',
                location.pathname === '/app/you'
                  ? 'text-[var(--color-carbon)]'
                  : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
            >
              {t('sidebar.you')}
            </span>
            <ChevronRight
              className={cn(
                'w-4 h-4 shrink-0 transition-all group-hover:translate-x-0.5',
                location.pathname === '/app/you'
                  ? 'text-[var(--color-carbon)]'
                  : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
              aria-hidden
            />
          </div>
        </button>
      )}
      {/* Collapsed: just the carbon avatar centered (still opens the workspace) */}
      {collapsed && userId && (
        <button
          type="button"
          onClick={() => navigate('/app/you')}
          aria-label={t('layout.sidebar.openWorkspace')}
          aria-current={location.pathname === '/app/you' ? 'page' : undefined}
          className={cn(
            'w-full px-4 py-3 border-b border-[var(--rule)] flex justify-center transition-colors',
            location.pathname === '/app/you'
              ? 'bg-[var(--bg-elevated)]'
              : 'hover:bg-[var(--bg-elevated)]',
          )}
        >
          <RingAvatar species="carbon" label={userLabel} size="sm" title={userLabel} />
        </button>
      )}

      {/* Agent list — grouped by team (spec §11); teams are sections inside
          the list itself, the old TeamFilterBar chip filter is retired. */}
      <ScrollArea className="flex-1">
        <AgentList collapsed={collapsed} />
      </ScrollArea>


      {/* Navigation Items */}
      <div className="px-3 py-2 border-t border-[var(--rule)] space-y-1">
        {!collapsed ? (
          <>
            {/* Mode Switcher — hidden (SHOW_MODE_SWITCHER); logic preserved */}
            {SHOW_MODE_SWITCHER && (
            <div className="relative">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowModePopup(!showModePopup)}
                className={cn('w-full justify-start gap-2', NAV_ITEM)}
              >
                {mode === 'local' ? (
                  <Monitor className="w-4 h-4" />
                ) : (
                  <Cloud className="w-4 h-4" />
                )}
                {mode === 'local' ? t('sidebar.local') : t('sidebar.cloud')}
              </Button>
              {showModePopup && (
                <div className="absolute bottom-full left-0 mb-1 w-full p-3 rounded-lg border shadow-lg z-50"
                  style={{
                    backgroundColor: 'var(--bg-secondary)',
                    borderColor: 'var(--border-default)',
                  }}>
                  <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
                    {t('layout.sidebar.currentMode', {
                      mode: mode === 'local' ? t('sidebar.localMode') : t('sidebar.cloudMode'),
                    })}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={handleSwitchMode}
                  >
                    <RotateCcw className="w-3 h-3 mr-1" />
                    {t('layout.sidebar.switchTo', {
                      mode: mode === 'local' ? t('sidebar.cloud') : t('sidebar.local'),
                    })}
                  </Button>
                </div>
              )}
            </div>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/app/dashboard')}
              onMouseEnter={prefetchDashboard}
              onFocus={prefetchDashboard}
              className={cn(
                'w-full justify-start gap-2',
                NAV_ITEM,
                location.pathname === '/app/dashboard' &&
                  NAV_ITEM_ACTIVE,
              )}
            >
              <LayoutDashboard className="w-4 h-4" />
              {t('sidebar.dashboard')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/app/settings')}
              className={cn(
                'w-full justify-start gap-2',
                NAV_ITEM,
                location.pathname === '/app/settings' &&
                  NAV_ITEM_ACTIVE,
              )}
            >
              <Sliders className="w-4 h-4" />
              {t('sidebar.settings')}
            </Button>
            {features.showSystemPage && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate('/app/system')}
                className={cn(
                  'w-full justify-start gap-2',
                  NAV_ITEM,
                  location.pathname === '/app/system' &&
                    NAV_ITEM_ACTIVE,
                )}
              >
                <Server className="w-4 h-4" />
                {t('sidebar.system')}
              </Button>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center gap-1">
            {SHOW_MODE_SWITCHER && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setShowModePopup(!showModePopup)}
              title={mode === 'local' ? t('sidebar.localMode') : t('sidebar.cloudMode')}
              className={NAV_ITEM}
            >
              {mode === 'local' ? (
                <Monitor className="w-4 h-4" />
              ) : (
                <Cloud className="w-4 h-4" />
              )}
            </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/app/dashboard')}
              onMouseEnter={prefetchDashboard}
              onFocus={prefetchDashboard}
              title={t('sidebar.dashboard')}
              className={cn(
                NAV_ITEM,
                location.pathname === '/app/dashboard' &&
                  NAV_ITEM_ACTIVE,
              )}
            >
              <LayoutDashboard className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/app/settings')}
              title={t('sidebar.settings')}
              className={cn(
                NAV_ITEM,
                location.pathname === '/app/settings' &&
                  NAV_ITEM_ACTIVE,
              )}
            >
              <Sliders className="w-4 h-4" />
            </Button>
            {features.showSystemPage && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => navigate('/app/system')}
                title={t('sidebar.system')}
                className={cn(
                  NAV_ITEM,
                  location.pathname === '/app/system' &&
                    NAV_ITEM_ACTIVE,
                )}
              >
                <Server className="w-4 h-4" />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Footer Actions */}
      <div className="p-3 border-t border-[var(--rule)] space-y-2">
        {!collapsed ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              className={cn('w-full justify-start gap-2', NAV_ITEM_DANGER)}
            >
              <LogOut className="w-4 h-4" />
              {t('sidebar.logout')}
            </Button>
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-[var(--rule)]">
              <ThemeToggle />
              <LanguageToggle />
              {isMobile && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setShowFeedback(true)}
                  title={t('feedback.title')}
                  className={NAV_ITEM}
                >
                  <MessageSquarePlus className="w-4 h-4" />
                </Button>
              )}
              <span className="flex-1 text-center text-[9px] text-[var(--text-tertiary)] font-mono tracking-wider truncate">
                {t('sidebar.poweredBy')}
              </span>
              <span className="text-[9px] text-[var(--text-tertiary)] font-mono tracking-wider">v{__APP_VERSION__}</span>
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              title={t('sidebar.logout')}
              className={NAV_ITEM_DANGER}
            >
              <LogOut className="w-4 h-4" />
            </Button>
            <ThemeToggle />
          </div>
        )}
      </div>
    </aside>
  );
}
