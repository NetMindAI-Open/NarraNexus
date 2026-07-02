/**
 * @file_name: HealthStatusBar.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Overall health status bar for the System page
 *
 * Displays a summary banner indicating whether all services are healthy,
 * some are unhealthy, or services are unavailable.
 */

import { CheckCircle2, AlertTriangle, XCircle, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { OverallHealth } from '@/types/platform';

interface HealthStatusBarProps {
  health: OverallHealth | null;
  isLoading: boolean;
}

export function HealthStatusBar({ health, isLoading }: HealthStatusBarProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className={cn(BAR_BASE, 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)]')}>
        <Loader2 className="w-4 h-4 animate-spin" />
        <span>{t('system.health.checking')}</span>
      </div>
    );
  }

  if (!health) {
    return (
      <div className={cn(BAR_BASE, 'bg-[var(--color-error)]/10 text-[var(--color-error)]')}>
        <XCircle className="w-4 h-4" />
        <span>{t('system.health.unavailable')}</span>
      </div>
    );
  }

  if (health.allHealthy) {
    return (
      <div className={cn(BAR_BASE, 'bg-[var(--color-success)]/10 text-[var(--color-success)]')}>
        <CheckCircle2 className="w-4 h-4" />
        <span>{t('system.health.allHealthy')}</span>
      </div>
    );
  }

  const unhealthyCount = health.services.filter(
    (s) => s.state !== 'healthy',
  ).length;

  return (
    <div className={cn(BAR_BASE, 'bg-[var(--color-warning)]/10 text-[var(--color-warning)]')}>
      <AlertTriangle className="w-4 h-4" />
      <span>
        {unhealthyCount > 1
          ? t('system.health.unhealthyPlural', { count: unhealthyCount })
          : t('system.health.unhealthy', { count: unhealthyCount })}
      </span>
    </div>
  );
}

const BAR_BASE =
  'flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium';

export type { HealthStatusBarProps };
