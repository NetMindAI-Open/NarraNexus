/**
 * @file_name: TeamMessageProcess.tsx
 * @date: 2026-07-31
 * @description: Per-message "view reasoning & tools" disclosure for team
 * transcript bubbles — the single-chat MessageBubble affordance, backed by
 * the message's own `event_id` (bus_messages.event_id, stamped by the
 * trigger). History lives on the message; the roster only covers the
 * latest turn.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Sparkles } from 'lucide-react';
import { TurnTimeline } from '../TurnTimeline';
import { isProcessEvent, useTurnDetail } from './useTurnDetail';

export function TeamMessageProcess({
  agentId,
  eventId,
}: {
  agentId: string;
  eventId: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const detail = useTurnDetail(agentId, eventId, open);
  const loading = open && detail === null;

  return (
    <div className="mt-2 border-t border-[var(--border-subtle)] pt-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
      >
        {loading ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Sparkles className="h-3 w-3" />
        )}
        <span className="font-medium">
          {open ? t('chat.message.hideReasoning') : t('chat.message.viewReasoning')}
        </span>
      </button>
      {open && detail?.kind === 'ready' && (
        <div className="mt-2">
          <TurnTimeline events={detail.events.filter(isProcessEvent)} />
        </div>
      )}
      {open && detail?.kind === 'empty' && (
        <div className="mt-2 text-xs text-[var(--text-tertiary)]">
          {t('chat.team.noProcess')}
        </div>
      )}
      {open && detail?.kind === 'error' && (
        <div className="mt-2 text-xs text-[var(--text-tertiary)]">
          {t('chat.team.detailLoadFailed')}
        </div>
      )}
    </div>
  );
}
