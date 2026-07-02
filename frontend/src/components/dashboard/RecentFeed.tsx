/**
 * @file_name: RecentFeed.tsx
 * @description: v2.1 — last 3 events for the agent, rendered as a compact
 * feed at the bottom of the card. Collapsible via useExpanded.
 */
import { useTranslation } from 'react-i18next';
import type { RecentEvent } from '@/types';
import { useExpanded } from './expandState';

const KIND_ICON: Record<RecentEvent['kind'], string> = {
  completed: '✓',
  running: '▶',
  failed: '⚠',
  chat: '💬',
  other: '·',
};

const KIND_COLOR: Record<RecentEvent['kind'], string> = {
  completed: 'text-[var(--color-green-500)]',
  running: 'text-sky-600',
  failed: 'text-[var(--color-red-500)]',
  chat: 'text-gray-600',
  other: 'text-gray-500',
};

function formatTime(iso: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function RecentFeed({ agentId, events }: { agentId: string; events: RecentEvent[] }) {
  const { t } = useTranslation();
  const { expanded, toggle } = useExpanded(`${agentId}:section:recent`, false);

  if (!events || events.length === 0) {
    return null;
  }

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        className="flex w-full items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        aria-expanded={expanded}
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        {t('dashboard.recent.title', { count: events.length })}
      </button>
      {expanded && (
        <ul className="mt-1 ml-3 space-y-0.5 border-l-2 border-[var(--rule)] pl-2">
          {events.map((ev) => (
            <li
              key={ev.event_id}
              className="flex items-center gap-2 text-[11px]"
              data-testid={`recent-event-${ev.kind}`}
            >
              <span className={`font-mono ${KIND_COLOR[ev.kind]}`}>{KIND_ICON[ev.kind]}</span>
              <span className="font-mono text-[var(--text-secondary)]">{formatTime(ev.created_at)}</span>
              <span className="truncate">{ev.verb}{ev.target ? `: ${ev.target}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
