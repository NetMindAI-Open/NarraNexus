/**
 * @file_name: BookmarkPanelHost.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Renders the drawer panel for the active rail tab, looked up in the PANELS registry.
 *
 * The host knows no panel by name: builtin panels are registered by
 * `platform/builtin.ts`, plugins add theirs. An id without a registered
 * panel renders nothing (an empty drawer body) rather than crashing — a
 * plugin may have added a strip tab and then been disabled.
 */
import { Suspense, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { PANELS, useRegistryEntries } from '@/platform/registries';
import { markTabOpened, type AtomicTabId } from './tabs';

function PanelFallback() {
  const { t } = useTranslation();
  return (
    <div className="h-full flex items-center justify-center py-16">
      <Loader2
        className="w-5 h-5 animate-spin"
        style={{ color: 'var(--text-tertiary)' }}
        aria-label={t('bookmarks.panelHost.loading')}
      />
    </div>
  );
}

export interface BookmarkPanelHostProps {
  tab: AtomicTabId;
  agentId: string;
}

export function BookmarkPanelHost({ tab, agentId }: BookmarkPanelHostProps) {
  // Opening a tab counts as seeing its news — clear its info highlights.
  useEffect(() => {
    markTabOpened(agentId, tab);
  }, [agentId, tab]);
  // Subscribed for re-render only: a panel registered after mount shows up.
  void useRegistryEntries(PANELS);
  const Panel = PANELS.get(tab)?.component;

  return (
    <div className="flex flex-col h-full min-h-0">
      <Suspense fallback={<PanelFallback />}>
        {Panel ? <Panel agentId={agentId} /> : null}
      </Suspense>
    </div>
  );
}
