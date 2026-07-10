/**
 * @file_name: BusFailuresSection.tsx
 * @author: Bin Liang
 * @date: 2026-07-03
 * @description: Messages the agent permanently gave up on (upstream #52).
 *
 * Backend parks a bus message after 3 failed processing attempts
 * (`bus_message_failures`, poison threshold) and writes a SYSTEM_NOTICE the
 * owner previously had no way to see. This section is the recovery surface:
 * it lists the parked messages with their last error and a retry action
 * (clears the failure row so the next poll cycle re-delivers). Opening the
 * section consumes the matching unread notices — the notice exists to bring
 * the owner here, so viewing IS the read.
 *
 * Renders nothing when the agent has no parked failures: zero noise on the
 * happy path.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, RotateCcw, Loader2 } from 'lucide-react';
import { Badge, Button } from '@/components/ui';
import { api } from '@/lib/api';
import { formatRelativeTime } from '@/lib/utils';
import type { BusFailureItem } from '@/types/api';

interface BusFailuresSectionProps {
  agentId: string;
}

export function BusFailuresSection({ agentId }: BusFailuresSectionProps) {
  const { t } = useTranslation();
  const [failures, setFailures] = useState<BusFailureItem[]>([]);
  const [retrying, setRetrying] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!agentId) return;
    try {
      const res = await api.getBusFailures(agentId);
      const items = res.failures ?? [];
      setFailures(items);
      if (items.length > 0) {
        // Viewing the failures consumes the notices that announced them.
        const notices = await api.getNotices(true);
        await Promise.allSettled(
          (notices.notices ?? [])
            .filter((n) => n.source?.type === 'message_bus_failure')
            .map((n) => api.markNoticeRead(n.message_id)),
        );
      }
    } catch {
      // Non-critical surface — the inbox itself must never break over this.
      setFailures([]);
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRetry = async (messageId: string) => {
    setRetrying(messageId);
    try {
      await api.retryBusFailure(agentId, messageId);
      setFailures((prev) => prev.filter((f) => f.message_id !== messageId));
    } catch {
      // Leave the row; the user can retry the retry.
    } finally {
      setRetrying(null);
    }
  };

  if (failures.length === 0) return null;

  return (
    <div
      data-testid="bus-failures-section"
      className="mb-3 rounded-lg border px-3 py-2"
      style={{ borderColor: 'var(--status-error-border, #f0b4b4)' }}
    >
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4" style={{ color: 'var(--status-error, #c0392b)' }} />
        <span className="text-sm font-medium">{t('inbox.busFailures.title')}</span>
        <Badge variant="warning">{failures.length}</Badge>
      </div>
      <ul className="space-y-2">
        {failures.map((f) => (
          <li key={f.message_id} className="text-xs flex items-start gap-2">
            <div className="flex-1 min-w-0">
              <div className="truncate font-medium">
                {f.channel_id} · {f.from_agent}
              </div>
              <div className="truncate" style={{ color: 'var(--text-tertiary)' }}>
                {f.last_error || t('inbox.busFailures.unknownError')}
              </div>
              <div style={{ color: 'var(--text-tertiary)' }}>
                {t('inbox.busFailures.attempts', { count: f.retry_count })}
                {f.last_retry_at ? ` · ${formatRelativeTime(f.last_retry_at)}` : ''}
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={retrying === f.message_id}
              onClick={() => handleRetry(f.message_id)}
              aria-label={t('inbox.busFailures.retry')}
            >
              {retrying === f.message_id ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <RotateCcw className="w-3 h-3" />
              )}
              <span className="ml-1">{t('inbox.busFailures.retry')}</span>
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
