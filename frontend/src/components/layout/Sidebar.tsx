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
import { Button, ThemeToggle } from '@/components/ui';
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

  const handleLogout = () => {
    if (!confirm('Are you sure you want to logout?')) return;
    wipeAllSessionData();
    window.location.href = '/login';
  };

  const handleClearHistory = async () => {
    if (!confirm('Clear all conversation history?')) return;

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
        'border-r border-[var(--border-default)]',
        'transition-all duration-400 ease-out',
        collapsed ? 'w-[72px]' : 'w-72'
      )}
    >
      {/* Gradient edge glow */}
      <div className="absolute top-0 right-0 bottom-0 w-px bg-gradient-to-b from-transparent via-[var(--accent-primary)]/20 to-transparent" />

      {/* Header — full-width logo replaces the old icon-tile + wordmark stack.
          Collapsed state hides the logo and keeps only the toggle button. */}
      <div className="p-4 border-b border-[var(--border-subtle)]">
        <div className="flex items-center justify-between gap-2">
          {!collapsed && (
            <img
              src="/logo.png"
              alt="NarraNexus"
              className="h-12 w-auto object-contain animate-fade-in"
            />
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

      {/* User Info */}
      {!collapsed && (
        <div className="px-4 py-3 border-b border-[var(--border-subtle)]">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-10 h-10 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center border border-[var(--border-default)]">
                <User className="w-5 h-5 text-[var(--text-secondary)]" />
              </div>
              <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[var(--color-success)] border-2 border-[var(--bg-secondary)]" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-[var(--text-primary)] truncate font-[family-name:var(--font-mono)]">
                {userId}
              </p>
              <p className="text-[10px] text-[var(--color-success)] uppercase tracking-wider font-medium">Online</p>
            </div>
          </div>
        </div>
      )}

      {/* Agents List */}
      <div className="flex-1 overflow-y-auto">
        <AgentList collapsed={collapsed} />
      </div>

      {/* Navigation Items */}
      <div className="px-3 py-2 border-t border-[var(--border-subtle)] space-y-1">
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
      <div className="p-3 border-t border-[var(--border-subtle)] space-y-2">
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
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-[var(--border-subtle)]">
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
