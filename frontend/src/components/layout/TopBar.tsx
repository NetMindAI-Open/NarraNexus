/**
 * TopBar — the narrow (36px) global status strip above the whole app.
 *
 * Holds only glanceable, GLOBAL state + one global action, so it never fights
 * the sidebar / bookmark strip / chat header for attention:
 *   - left:  binding-dot + a location breadcrumb (which agent / which page)
 *   - right: runtime + connection status (LOCAL/CLOUD + online dot)
 *   - right: ⌘K command palette trigger (jump to any agent or page)
 *
 * Deliberately NOT here (would duplicate the sidebar or carry per-agent, not
 * global, state): user menu / theme / logout (sidebar footer), the per-agent
 * token cost chip (chat header). A global unread bell is a planned addition
 * once a cross-agent unread rollup exists.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { BindingDot } from '@/components/nm';
import { useConfigStore, useUIStore } from '@/stores';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { CommandPalette } from './CommandPalette';

/** Map a route to a breadcrumb i18n key (agent name handled separately). */
function pageLabelKey(pathname: string): string | null {
  if (pathname.startsWith('/app/dashboard')) return 'layout.topBar.crumbDashboard';
  if (pathname.startsWith('/app/settings')) return 'layout.topBar.crumbSettings';
  if (pathname.startsWith('/app/system')) return 'layout.topBar.crumbSystem';
  if (pathname.startsWith('/app/manage-agents')) return 'layout.topBar.crumbManageAgents';
  if (pathname.startsWith('/app/teams')) return 'layout.topBar.crumbTeam';
  if (pathname.startsWith('/app/bundle')) return 'layout.topBar.crumbBundle';
  return null; // chat
}

export function TopBar() {
  const { t } = useTranslation();
  const location = useLocation();
  const agents = useConfigStore((s) => s.agents);
  const agentId = useConfigStore((s) => s.agentId);
  const mode = useRuntimeStore((s) => s.mode);
  const toggleMobileNav = useUIStore((s) => s.toggleMobileNav);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Global ⌘K / Ctrl+K to open the palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const pageKey = pageLabelKey(location.pathname);
  const page = pageKey ? t(pageKey) : null;
  const agentName = agents.find((a) => a.agent_id === agentId)?.name || (agentId ? agentId : null);
  // On chat → show the agent; on a sub-page → show the page name.
  const crumb = page ?? agentName ?? t('layout.topBar.crumbChat');
  const crumbContext = page ? t('layout.topBar.contextApp') : t('layout.topBar.contextChat');

  const connLabel = mode === 'local' ? 'LOCAL' : mode ? 'CLOUD' : '—';

  return (
    <>
      <div
        className="flex h-9 shrink-0 items-center justify-between px-3 bg-[var(--nm-card)] border-b border-[var(--nm-hairline)]"
        data-help-id="topbar"
      >
        {/* left — hamburger (mobile only) + binding-dot + breadcrumb */}
        <div className="flex min-w-0 items-center gap-2.5">
          <button
            type="button"
            onClick={toggleMobileNav}
            aria-label={t('layout.topBar.openMenu')}
            className="-ml-1 flex h-7 w-7 items-center justify-center rounded-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--color-carbon)] md:hidden"
          >
            <Menu className="h-4 w-4" />
          </button>
          <BindingDot size={6} />
          <span className="truncate font-[family-name:var(--font-mono)] text-[11px] tracking-[0.06em] text-[var(--nm-ink)]">
            <span className="text-[var(--nm-ink30)]">{crumbContext} / </span>
            {crumb}
          </span>
        </div>

        {/* right — connection status + ⌘K */}
        <div className="flex shrink-0 items-center gap-3.5">
          <span
            title={t('layout.topBar.runtimeTitle', {
              mode:
                connLabel === 'LOCAL'
                  ? t('layout.topBar.runtimeLocal')
                  : connLabel === 'CLOUD'
                    ? t('layout.topBar.runtimeCloud')
                    : t('layout.topBar.runtimeUnknown'),
            })}
            className="inline-flex items-center gap-1.5 font-[family-name:var(--font-mono)] text-[10px] tracking-[0.12em] text-[var(--text-tertiary)]"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
            {connLabel}
          </span>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            title={t('layout.topBar.commandPaletteTitle')}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] px-2 py-1 font-[family-name:var(--font-mono)] text-[10px] text-[var(--text-tertiary)] transition-colors hover:text-[var(--color-carbon)] hover:border-[var(--color-carbon)]"
          >
            <span className="text-[11px] leading-none">⌘</span>K
            <span className="ml-0.5 text-[var(--nm-ink30)]">{t('layout.topBar.search')}</span>
          </button>
        </div>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
