/**
 * Sidebar — the app's three-zone shell.
 *
 * Zone 1: logo + collapse (panel icon). Zone 2: global nav (New / Export /
 * Dashboard / Marketplace / Workspace / Settings) then the Chats list
 * (teams + agents, owned by AgentList — the sidebar stays "a shell, not a
 * list owner"). Zone 3: the user row opening an identity-only account
 * popover (account / version / logout) + the Find Us entry. Theme and
 * language live in Settings → Personalization, not here.
 *
 * Collapse hides the whole aside (uiStore.sidebarCollapsed); the expand
 * button renders in the chat header / a floating chip, outside this tree.
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useIsMobile } from '@/hooks/useMediaQuery';
import {
  LogOut,
  PanelLeft,
  Monitor,
  Cloud,
  RotateCcw,
  User,
  Users,
  ChevronsUpDown,
} from 'lucide-react';
import { BetaBadge, ScrollArea, useConfirm } from '@/components/ui';
import { FeedbackDialog } from '@/components/ui/FeedbackDialog';
import { RingAvatar, StatusDot } from '@/components/nm';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/hooks';
import { useAgentImported, useDismissOnOutside } from '@/hooks';
import { useConfigStore, useRuntimeStore, useUIStore } from '@/stores';
import { cn } from '@/lib/utils';
import { SIDEBAR, SIDEBAR_SECTIONS, sortedSidebarItems, useRegistryEntries } from '@/platform/registries';
import { SlotOutlet } from '@/platform/SlotOutlet';
import { useWhenContext } from '@/platform/whenContext';
import { wipeAllSessionData } from '@/lib/sessionWipe';
import { AgentList } from './AgentList';
import { CreateMenu } from './CreateMenu';
import { ImportAgentModal } from './ImportAgentModal';
import { FIND_US_URL } from './TopBar';

// Nav rows are LIST ROWS — same interaction ladder as the agent/team rows
// below them (design_system.md §2.5): hover = --nm-row-hover, current page =
// --nm-row-active. Keeping them off the warm control family avoids two hue
// systems inside one sidebar.
const NAV_ROW =
  'w-full flex items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left transition-colors text-[var(--nm-ink70)] hover:bg-[var(--nm-row-hover)] hover:text-[var(--nm-ink)]';
const NAV_ROW_ACTIVE = 'bg-[var(--nm-row-active)] text-[var(--nm-ink)]';

export function Sidebar() {
  const [showModePopup, setShowModePopup] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // Mobile-only feedback entry. Desktop uses the floating FeedbackButton
  // (bottom-right, by the help "?"); on mobile that corner is the composer's,
  // so the drawer footer carries it instead. Exactly one entry per viewport.
  const [showFeedback, setShowFeedback] = useState(false);
  // Mobile (< md): the sidebar is an off-canvas drawer toggled from the
  // mobile top strip. Desktop (md+): collapse hides the aside entirely.
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const location = useLocation();
  const { userId, displayName } = useConfigStore();
  const netmindToken = useConfigStore((s) => s.netmindToken);
  // user_id is an opaque NetMind userSystemCode (32-hex) in cloud mode, not
  // human-readable. Show the NetMind nickname when we have it; fall back to
  // user_id (local mode, where it IS the chosen username).
  const userLabel = displayName || userId;
  const { mode, features, setMode, setCloudApiUrl } = useRuntimeStore();
  const handleImportApplied = useAgentImported();
  // Import-from-other-source is local-only: the scanner reads the user's
  // filesystem, and detect/scan 503 on cloud (see backend/routes/migrate.py).
  const isLocalMode = mode === 'local';
  // Nav rows come from the sidebar registry (builtin rows are registered by
  // platform/builtin.ts; a plugin registering later re-renders this list).
  const navItems = sortedSidebarItems(features, useRegistryEntries(SIDEBAR));
  const whenCtx = useWhenContext({});

  // The cloud/local mode switcher is hidden — we don't want users choosing
  // the deployment mode. All the switching logic (handleSwitchMode, mode
  // state) is kept intact behind this flag so it can be re-enabled by
  // flipping to true; only the UI entry points are gated.
  const SHOW_MODE_SWITCHER = false;
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { isDark } = useTheme();
  const { t } = useTranslation();
  const accountRef = useDismissOnOutside<HTMLDivElement>(accountOpen, () => setAccountOpen(false));

  const modeLabel = mode === 'local' ? t('sidebar.local') : t('sidebar.cloud');

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
    setAccountOpen(false);
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

  const accountNavigate = (to: string) => {
    setAccountOpen(false);
    navigate(to);
  };

  return (
    <aside
      className={cn(
        'flex flex-col relative',
        // NM canonical (FinChats:461): chat-list container bg = var(--nm-paper).
        // Rows sit on paper directly with rounded highlight when active.
        'bg-[color:var(--nm-paper)]',
        'border-r border-[color:var(--nm-hairline)]',
        // Mobile (< md): off-canvas drawer below the 36px mobile top strip.
        // Height comes from top-9 + bottom-0 (NOT h-full, which would overflow
        // 36px below the viewport and clip the footer).
        'fixed top-9 bottom-0 left-0 z-40 w-[272px] transition-transform duration-300 ease-out',
        mobileNavOpen ? 'translate-x-0 shadow-[var(--nm-elev-3)]' : '-translate-x-full',
        // Tablet/desktop (md+): back in normal flow, full height; collapse
        // hides the whole aside (the expand affordance lives outside it).
        'md:static md:top-auto md:bottom-auto md:z-auto md:h-full md:translate-x-0 md:shadow-none',
        sidebarCollapsed ? 'md:hidden' : 'md:flex',
      )}
    >
      {confirmDialog}
      {isMobile && <FeedbackDialog isOpen={showFeedback} onClose={() => setShowFeedback(false)} />}
      {importOpen && (
        <ImportAgentModal
          onClose={() => setImportOpen(false)}
          onApplied={handleImportApplied}
        />
      )}

      {/* ── Zone 1: logo + collapse ─────────────────────────────────────── */}
      <div className="px-4 pt-3.5 pb-2.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <img
            src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
            alt="NarraNexus"
            className="h-9 w-auto object-contain shrink-0"
          />
          <BetaBadge className="shrink-0" />
        </div>
        <button
          type="button"
          onClick={toggleSidebar}
          title={t('sidebar.collapseTitle')}
          aria-label={t('sidebar.collapseTitle')}
          className="shrink-0 hidden md:inline-flex items-center justify-center w-7 h-7 rounded-[var(--radius-sm)] text-[var(--nm-ink50)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)] transition-colors"
        >
          <PanelLeft className="w-4 h-4" />
        </button>
      </div>

      {/* ── Zone 2a: global nav ─────────────────────────────────────────── */}
      <div className="px-2 pb-2 flex flex-col gap-px border-b border-[var(--nm-hairline)]">
        <span data-help-id="sidebar.create-agent">
          <CreateMenu
            // The "+" opens the creation studio's fork rather than creating
            // straight away: a one-click create is what left users unaware
            // they had created an agent at all. The blank path there calls
            // the same useCreateAgent().
            onCreateAgent={() => navigate('/app/agents/new')}
            onCreateTeam={() => navigate('/app/teams/new')}
            onImportBundle={() => navigate('/app/bundle/import')}
            onImportAgent={isLocalMode ? () => setImportOpen(true) : undefined}
          />
        </span>
        {navItems.map(({ id, value }) => {
          const Icon = value.icon;
          const active = value.isActive ? value.isActive(location) : location.pathname === value.to;
          return (
            <button
              key={id}
              type="button"
              onClick={() => navigate(value.to)}
              onMouseEnter={value.prefetch}
              onFocus={value.prefetch}
              title={value.titleKey ? t(value.titleKey) : undefined}
              data-help-id={value.helpId}
              className={cn(NAV_ROW, active && NAV_ROW_ACTIVE)}
            >
              <Icon className="w-4 h-4 shrink-0" />
              {t(value.labelKey)}
            </button>
          );
        })}
      </div>
      {/* Plugin sections under the nav (ui.sidebarSections), gated by `when`. */}
      <SlotOutlet registry={SIDEBAR_SECTIONS} ctx={whenCtx} agentId={null} as="div" className="px-2 pb-1 space-y-1" />

      {/* ── Zone 2b: Chats (teams + agents, owned by AgentList) ─────────── */}
      <ScrollArea className="flex-1">
        <AgentList />
      </ScrollArea>

      {/* ── Zone 3: user row + account popover + Find Us ────────────────── */}
      <div ref={accountRef} className="p-2 border-t border-[var(--nm-hairline)] relative">
        {accountOpen && (
          <div
            className={cn(
              'absolute bottom-full left-2 right-2 mb-1.5 z-50 p-1.5',
              'rounded-[var(--radius-md)] border shadow-[0_8px_24px_rgba(0,0,0,0.14)]',
              'bg-[var(--nm-card)] border-[var(--nm-hairline)]',
            )}
          >
            <div className="px-2.5 pt-2 pb-1.5 border-b border-[var(--nm-hairline)] mb-1">
              <div className="text-[13px] font-semibold text-[var(--nm-ink)] truncate" title={userLabel}>
                {userLabel}
              </div>
              <div className="text-[10px] text-[var(--nm-ink50)]">
                {t('sidebar.online')} · {modeLabel} · v{__APP_VERSION__}
              </div>
            </div>
            {/* Identity actions only. Theme and language live in Settings →
                Personalization; the workspace has its own nav row. Keeping a
                second settings surface here is what made users ask what the
                difference between the two was. */}
            {netmindToken && (
              <AccountItem
                icon={<User className="w-3.5 h-3.5" />}
                label={t('sidebar.account')}
                onClick={() => accountNavigate('/app/settings?tab=account')}
              />
            )}
            {isMobile && (
              <AccountItem
                icon={<Users className="w-3.5 h-3.5" />}
                label={t('feedback.title')}
                onClick={() => { setAccountOpen(false); setShowFeedback(true); }}
              />
            )}
            {SHOW_MODE_SWITCHER && (
              <AccountItem
                icon={mode === 'local' ? <Monitor className="w-3.5 h-3.5" /> : <Cloud className="w-3.5 h-3.5" />}
                label={t('layout.sidebar.switchTo', {
                  mode: mode === 'local' ? t('sidebar.cloud') : t('sidebar.local'),
                })}
                onClick={() => setShowModePopup(!showModePopup)}
              />
            )}
            {SHOW_MODE_SWITCHER && showModePopup && (
              <button
                type="button"
                onClick={handleSwitchMode}
                className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left text-[var(--nm-ink70)] hover:bg-[var(--nm-paper-warm)]"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                {t('layout.sidebar.currentMode', {
                  mode: mode === 'local' ? t('sidebar.localMode') : t('sidebar.cloudMode'),
                })}
              </button>
            )}
            <div className="my-1 mx-1 border-t border-[var(--nm-hairline)]" />
            <AccountItem
              icon={<LogOut className="w-3.5 h-3.5" />}
              label={t('sidebar.logout')}
              danger
              onClick={handleLogout}
            />
            <div className="px-2.5 pt-1 pb-0.5 text-[9px] text-[var(--nm-ink30)] font-mono tracking-wider truncate">
              {t('sidebar.poweredBy')}
            </div>
          </div>
        )}

        <div className="flex items-stretch gap-1.5">
          <button
            type="button"
            onClick={() => setAccountOpen((v) => !v)}
            title={t('sidebar.accountTitle')}
            aria-label={t('sidebar.accountTitle')}
            className={cn(
              'flex flex-1 min-w-0 items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] text-left transition-colors',
              accountOpen ? 'bg-[var(--nm-raised)]' : 'hover:bg-[var(--nm-paper-warm)]',
            )}
          >
            <RingAvatar species="carbon" label={userLabel || '?'} size="sm" />
            <span className="flex-1 min-w-0 flex flex-col justify-center gap-0.5">
              <span className="text-[13px] font-semibold leading-tight text-[var(--nm-ink)] truncate" title={userLabel}>
                {userLabel}
              </span>
              <span className="flex items-center gap-1.5 text-[10px] leading-tight text-[var(--nm-ink50)]">
                <StatusDot status="success" size={6} />
                {t('sidebar.online')} · {modeLabel}
              </span>
            </span>
            <ChevronsUpDown className="w-3.5 h-3.5 shrink-0 text-[var(--nm-ink30)]" aria-hidden />
          </button>
          {/* Community entry — must stay a plain external link (no in-app
              routing); shortens the "sign up → join the community" path. */}
          <a
            href={FIND_US_URL}
            target="_blank"
            rel="noopener noreferrer"
            title={t('layout.topBar.findUsTitle')}
            data-help-id="sidebar.find-us"
            className="shrink-0 inline-flex flex-col items-center justify-center gap-1 px-2.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] text-[var(--nm-ink50)] no-underline text-[9px] font-medium transition-colors hover:text-[var(--nm-ink)] hover:border-[var(--border-strong)]"
          >
            <Users className="w-3.5 h-3.5" />
            {t('layout.topBar.findUs')}
          </a>
        </div>
      </div>
    </aside>
  );
}

function AccountItem({
  icon,
  label,
  hint,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left transition-colors',
        danger
          ? 'text-[var(--color-error)] hover:bg-[rgba(201,90,77,0.08)]'
          : 'text-[var(--nm-ink70)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]',
      )}
    >
      {icon}
      <span className="flex-1 min-w-0 truncate">{label}</span>
      {hint && (
        <span className="text-[10px] font-mono text-[var(--nm-ink30)] shrink-0">{hint}</span>
      )}
    </button>
  );
}
