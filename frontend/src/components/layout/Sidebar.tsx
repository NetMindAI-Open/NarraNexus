/**
 * Sidebar - Bioluminescent Terminal style
 * Agent selection and navigation with dramatic visual effects
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  User,
  LogOut,
  Trash2,
  ChevronLeft,
  ChevronRight,
  Sliders,
  Server,
  Monitor,
  Cloud,
  RotateCcw,
  LayoutDashboard,
} from 'lucide-react';
import { Button, ThemeToggle, ScrollArea, useConfirm } from '@/components/ui';
import { useTheme } from '@/hooks';
import { useConfigStore, useChatStore, useRuntimeStore, usePreloadStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

// v2.2 G1: prefetch the lazy DashboardPage chunk on hover/focus so click
// arrives to a warm cache. Static literal -> Vite resolves at build time,
// no injection risk.
const prefetchDashboard = () => {
  void import('@/pages/DashboardPage');
};
import { AgentList } from './AgentList';

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [showModePopup, setShowModePopup] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const { userId, agentId, logout } = useConfigStore();
  const { clearAll: clearChat } = useChatStore();
  const { mode, features, setMode, setCloudApiUrl } = useRuntimeStore();
  const clearPreload = usePreloadStore((s) => s.clearAll);
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { isDark } = useTheme();

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
      title: 'Log out',
      message: 'Are you sure you want to logout?',
      confirmText: 'Log out',
      danger: true,
    });
    if (!ok) return;
    wipeAllSessionData();
    window.location.href = '/login';
  };

  const handleClearHistory = async () => {
    const ok = await confirm({
      title: 'Clear history',
      message: 'Clear all conversation history?',
      confirmText: 'Clear',
      danger: true,
    });
    if (!ok) return;

    if (agentId) {
      try {
        const result = await api.clearHistory(agentId, userId);
        if (!result.success) {
          console.error('Failed to clear history:', result.error);
        }
      } catch (err) {
        console.error('Error clearing history from database:', err);
      }
    }

    clearChat();
  };

  return (
    <aside
      className={cn(
        'h-full flex flex-col relative',
        'bg-[var(--bg-secondary)]',
        'border-r border-[var(--rule)]',
        'transition-all duration-400 ease-out',
        collapsed ? 'w-[72px]' : 'w-72'
      )}
    >
      {confirmDialog}

      {/* Header — full-width logo replaces the old icon-tile + wordmark stack.
          Collapsed state hides the logo and keeps only the toggle button. */}
      <div className="p-4 border-b border-[var(--rule)]">
        <div className="flex items-center justify-between gap-2">
          {!collapsed && (
            <div className="flex items-center gap-0 animate-fade-in min-w-0">
              <img
                src={isDark ? '/logo-dark-mode.png' : '/logo-light-mode.png'}
                alt="NarraNexus"
                className="h-12 w-auto object-contain shrink-0"
              />
              <span className="text-[16px] font-medium leading-none text-[var(--text-primary)] font-[family-name:Inter,system-ui,sans-serif] tracking-[0.02em] truncate">
                NarraNexus
              </span>
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setCollapsed(!collapsed)}
            className={cn('shrink-0', collapsed && 'mx-auto')}
          >
            {collapsed ? (
              <ChevronRight className="w-4 h-4" />
            ) : (
              <ChevronLeft className="w-4 h-4" />
            )}
          </Button>
        </div>
      </div>

      {/* User Info — avatar on the left, two-line stack on the right.
          Use <div> not <p> to avoid inheriting global typography rules.
          The online dot inside the "Online" label is the single source of
          truth for status — no separate corner indicator on the avatar. */}
      {!collapsed && (
        <div className="px-4 py-3 border-b border-[var(--rule)]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 shrink-0 bg-[var(--bg-tertiary)] flex items-center justify-center border border-[var(--border-subtle)]">
              <User className="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            {/* Name + status stacked inside a 40px column matching the avatar,
                centered vertically so the pair sits on the avatar's midline. */}
            <div className="flex-1 min-w-0 h-10 flex flex-col justify-center gap-1">
              <div className="text-[13px] leading-none text-[var(--text-primary)] truncate font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                {userId}
              </div>
              <div className="flex items-center gap-1.5 text-[10px] leading-none text-[var(--text-tertiary)] uppercase tracking-[0.14em] font-[family-name:var(--font-mono)]">
                <span
                  className="w-1.5 h-1.5 rounded-full allow-circle bg-[var(--color-green-500)] shrink-0"
                  aria-hidden
                />
                <span>Online</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Agents List */}
      <ScrollArea className="flex-1">
        <AgentList collapsed={collapsed} />
      </ScrollArea>

      {/* Navigation Items */}
      <div className="px-3 py-2 border-t border-[var(--rule)] space-y-1">
        {!collapsed ? (
          <>
            {/* Mode Switcher */}
            <div className="relative">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowModePopup(!showModePopup)}
                className="w-full justify-start gap-2 text-[var(--text-secondary)]"
              >
                {mode === 'local' ? (
                  <Monitor className="w-4 h-4" />
                ) : (
                  <Cloud className="w-4 h-4" />
                )}
                {mode === 'local' ? 'Local' : 'Cloud'}
              </Button>
              {showModePopup && (
                <div className="absolute bottom-full left-0 mb-1 w-full p-3 rounded-lg border shadow-lg z-50"
                  style={{
                    backgroundColor: 'var(--bg-secondary)',
                    borderColor: 'var(--border-default)',
                  }}>
                  <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
                    Current: {mode === 'local' ? 'Local Mode' : 'Cloud Mode'}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={handleSwitchMode}
                  >
                    <RotateCcw className="w-3 h-3 mr-1" />
                    Switch to {mode === 'local' ? 'Cloud' : 'Local'}
                  </Button>
                </div>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/app/dashboard')}
              onMouseEnter={prefetchDashboard}
              onFocus={prefetchDashboard}
              className={cn(
                'w-full justify-start gap-2 text-[var(--text-secondary)]',
                location.pathname === '/app/dashboard' &&
                  'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
              )}
            >
              <LayoutDashboard className="w-4 h-4" />
              Dashboard
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/app/settings')}
              className={cn(
                'w-full justify-start gap-2 text-[var(--text-secondary)]',
                location.pathname === '/app/settings' &&
                  'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
              )}
            >
              <Sliders className="w-4 h-4" />
              Settings
            </Button>
            {features.showSystemPage && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate('/app/system')}
                className={cn(
                  'w-full justify-start gap-2 text-[var(--text-secondary)]',
                  location.pathname === '/app/system' &&
                    'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
                )}
              >
                <Server className="w-4 h-4" />
                System
              </Button>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setShowModePopup(!showModePopup)}
              title={mode === 'local' ? 'Local Mode' : 'Cloud Mode'}
            >
              {mode === 'local' ? (
                <Monitor className="w-4 h-4" />
              ) : (
                <Cloud className="w-4 h-4" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/app/dashboard')}
              onMouseEnter={prefetchDashboard}
              onFocus={prefetchDashboard}
              title="Dashboard"
              className={cn(
                location.pathname === '/app/dashboard' &&
                  'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
              )}
            >
              <LayoutDashboard className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/app/settings')}
              title="Settings"
              className={cn(
                location.pathname === '/app/settings' &&
                  'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
              )}
            >
              <Sliders className="w-4 h-4" />
            </Button>
            {features.showSystemPage && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => navigate('/app/system')}
                title="System"
                className={cn(
                  location.pathname === '/app/system' &&
                    'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
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
              onClick={handleClearHistory}
              className="w-full justify-start gap-2 text-[var(--text-secondary)] hover:text-[var(--color-error)] hover:bg-[var(--color-error)]/10"
            >
              <Trash2 className="w-4 h-4" />
              Clear History
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              className="w-full justify-start gap-2 text-[var(--text-secondary)] hover:text-[var(--color-error)] hover:bg-[var(--color-error)]/10"
            >
              <LogOut className="w-4 h-4" />
              Logout
            </Button>
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-[var(--rule)]">
              <ThemeToggle />
              <span className="flex-1 text-center text-[9px] text-[var(--text-tertiary)] font-mono tracking-wider truncate">
                Powered by NetMind.AI
              </span>
              <span className="text-[9px] text-[var(--text-tertiary)] font-mono tracking-wider">v1.0.0</span>
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={handleClearHistory}
              title="Clear History"
              className="hover:text-[var(--color-error)] hover:bg-[var(--color-error)]/10"
            >
              <Trash2 className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              title="Logout"
              className="hover:text-[var(--color-error)] hover:bg-[var(--color-error)]/10"
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
