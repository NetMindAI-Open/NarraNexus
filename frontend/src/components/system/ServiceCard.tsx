/**
 * @file_name: ServiceCard.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Service status card for the System page
 *
 * Displays a single service's status with indicator dot, port info,
 * and optional restart button.
 */

import { RotateCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, Button } from '@/components/ui';
import { cn } from '@/lib/utils';

type ServiceStatus =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'crashed'
  | 'healthy'
  | 'unhealthy'
  | 'unknown';

interface ServiceCardProps {
  label: string;
  status: ServiceStatus;
  port: number | null;
  lastError: string | null;
  onRestart?: () => void;
}

const STATUS_CONFIG: Record<
  ServiceStatus,
  { color: string; bg: string; pulse: boolean }
> = {
  healthy: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  running: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  starting: {
    color: 'bg-[var(--color-warning)]',
    bg: 'shadow-[0_0_8px_var(--color-warning)]',
    pulse: true,
  },
  crashed: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  unhealthy: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  stopped: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
  unknown: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
};

export function ServiceCard({
  label,
  status,
  port,
  lastError,
  onRestart,
}: ServiceCardProps) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[status];

  return (
    <Card variant="default">
      <CardContent className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {/* Header row: indicator + label */}
          <div className="flex items-center gap-2.5 mb-2">
            <span className="relative flex h-3 w-3">
              {config.pulse && (
                <span
                  className={cn(
                    'absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping',
                    config.color,
                  )}
                />
              )}
              <span
                className={cn(
                  'relative inline-flex rounded-full h-3 w-3',
                  config.color,
                  config.bg,
                )}
              />
            </span>
            <span className="text-sm font-semibold text-[var(--text-primary)] truncate">
              {label}
            </span>
          </div>

          {/* Status + port */}
          <div className="flex items-center gap-3 text-xs text-[var(--text-secondary)]">
            <span>{t(`system.serviceStatus.${status}`)}</span>
            {port != null && (
              <span className="font-mono text-[var(--text-tertiary)]">
                :{port}
              </span>
            )}
          </div>

          {/* Error message */}
          {lastError && (
            <p className="mt-2 text-xs text-[var(--color-error)] truncate">
              {lastError}
            </p>
          )}
        </div>

        {/* Restart button */}
        {onRestart && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onRestart}
            title={t('system.serviceCard.restart')}
            className="shrink-0"
          >
            <RotateCw className="w-4 h-4" />
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

export type { ServiceCardProps, ServiceStatus };
