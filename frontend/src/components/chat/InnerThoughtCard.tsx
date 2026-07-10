/**
 * @file_name: InnerThoughtCard.tsx
 * @author: Bin Liang
 * @date: 2026-07-03
 * @description: One "inner thought" (message_type=activity) as an expandable card.
 *
 * An activity row is written whenever a NON-chat trigger runs the agent and it
 * sent no user-facing reply (chat_module.py). Those triggers are diverse — a
 * scheduled job, an agent-to-agent bus message, an inbound IM on any channel,
 * a skill study — and previously every one rendered identically ("Message"),
 * so the tab was a wall of indistinguishable rows. Each source now has its own
 * COLOUR + NAME: a coloured left accent bar and a coloured dot + label make the
 * source scannable at a glance (icons are avoided — lucide has no brand logos,
 * and the name + colour carry the identity honestly). IM channels use their
 * brand name verbatim (WeChat / Slack / …); category sources (job / collab /
 * skill) use a localized label.
 *
 * Info is layered: the collapsed card shows source + time + a one-line summary;
 * the turn's full agent-loop steps load lazily by ``item.eventId`` via
 * ``api.getEventLog`` only on first expand (cached), with distinct
 * loading / load-failed / empty states.
 */

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, ChevronDown, Loader2, Brain, Wrench, CheckCircle2, TerminalSquare,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { TimelineItem } from '@/lib/buildTimeline';
import type { EventLogResponse, EventLogTimelineEntry } from '@/types/api';

interface SourceMeta {
  /** Brand name shown verbatim (IM channels). */
  label?: string;
  /** i18n key for category sources (job / collaboration / skill). */
  labelKey?: string;
  /** Accent colour — left bar + dot + name. */
  color: string;
}

// Each source gets a distinct colour so the Inner Thoughts list is scannable.
const SOURCE_META: Record<string, SourceMeta> = {
  wechat: { label: 'WeChat', color: '#07C160' },
  lark: { labelKey: 'chat.inner.source.lark', color: '#3370FF' },
  slack: { label: 'Slack', color: '#611F69' },
  telegram: { label: 'Telegram', color: '#229ED9' },
  discord: { label: 'Discord', color: '#5865F2' },
  narramessenger: { label: 'NarraMessenger', color: '#E8590C' },
  job: { labelKey: 'chat.inner.source.job', color: '#B8860B' },
  message_bus: { labelKey: 'chat.inner.source.collaboration', color: '#0D9488' },
  a2a: { labelKey: 'chat.inner.source.collaboration', color: '#0D9488' },
  skill_study: { labelKey: 'chat.inner.source.skill', color: '#7C3AED' },
  callback: { labelKey: 'chat.inner.source.callback', color: '#64748B' },
};
const DEFAULT_META: SourceMeta = { labelKey: 'chat.inner.source.activity', color: '#6B7280' };

type LoadState = 'idle' | 'loading' | 'ok' | 'error';

/** Prefer the structured timeline; fall back to legacy thinking + tool_calls. */
function toEntries(res: EventLogResponse): EventLogTimelineEntry[] {
  if (res.timeline && res.timeline.length) return res.timeline;
  const out: EventLogTimelineEntry[] = [];
  if (res.thinking) out.push({ type: 'thinking', content: res.thinking });
  for (const tc of res.tool_calls ?? []) {
    out.push({
      type: 'tool_call',
      tool_name: tc.tool_name,
      tool_input: tc.tool_input,
      tool_output: tc.tool_output,
    });
  }
  return out;
}

function EntryRow({ entry }: { entry: EventLogTimelineEntry }) {
  if (entry.type === 'tool_call') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <Wrench className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        <span className="font-mono break-all">{entry.tool_name}</span>
      </div>
    );
  }
  if (entry.type === 'tool_output') {
    return (
      <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
        <TerminalSquare className="w-3 h-3 mt-0.5 shrink-0" />
        <span className="break-words font-mono">{entry.content ?? entry.tool_output}</span>
      </div>
    );
  }
  if (entry.type === 'reply') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <CheckCircle2 className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--status-success, #2e7d32)' }} />
        <span className="break-words">{entry.content}</span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
      <Brain className="w-3 h-3 mt-0.5 shrink-0" />
      <span className="break-words italic">{entry.content}</span>
    </div>
  );
}

export function InnerThoughtCard({ item, agentId }: { item: TimelineItem; agentId: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [state, setState] = useState<LoadState>('idle');
  const [entries, setEntries] = useState<EventLogTimelineEntry[]>([]);

  const canExpand = !!item.eventId;
  const meta = SOURCE_META[item.workingSource ?? ''] ?? DEFAULT_META;
  const label = meta.label ?? t(meta.labelKey ?? 'chat.inner.source.activity');

  const toggle = useCallback(async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && (state === 'idle' || state === 'error') && item.eventId) {
      setState('loading');
      try {
        const res = await api.getEventLog(agentId, item.eventId);
        setEntries(toEntries(res));
        setState('ok');
      } catch {
        setEntries([]);
        setState('error');
      }
    }
  }, [expanded, state, item.eventId, agentId]);

  return (
    <div
      data-testid="inner-thought-card"
      className="mx-3 my-1.5 rounded-lg border pr-3 py-2 pl-3"
      style={{ borderColor: 'var(--border-subtle, #e5e5e5)', borderLeft: `3px solid ${meta.color}` }}
    >
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: meta.color }} />
        <span className="text-xs font-semibold" style={{ color: meta.color }}>{label}</span>
        <span className="text-[10px] ml-auto" style={{ color: 'var(--text-tertiary)' }}>
          {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
        {item.content}
      </div>

      {canExpand && (
        <button
          type="button"
          onClick={toggle}
          className="mt-1.5 flex items-center gap-1 text-[11px]"
          style={{ color: 'var(--text-tertiary)' }}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          {t('chat.inner.viewLoop')}
        </button>
      )}

      {expanded && (
        <div className="mt-2 space-y-1.5 pl-1 border-l" style={{ borderColor: 'var(--border-subtle, #e5e5e5)' }}>
          {state === 'loading' ? (
            <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              <Loader2 className="w-3 h-3 animate-spin" />
              {t('chat.inner.loading')}
            </div>
          ) : state === 'error' ? (
            <div className="text-xs" style={{ color: 'var(--status-error, #c0392b)' }}>
              {t('chat.inner.loadFailed')}
            </div>
          ) : entries.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {t('chat.inner.empty')}
            </div>
          ) : (
            entries.map((e, i) => <EntryRow key={i} entry={e} />)
          )}
        </div>
      )}
    </div>
  );
}
