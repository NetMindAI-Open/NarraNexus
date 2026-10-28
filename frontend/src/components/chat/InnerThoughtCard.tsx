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

import { Suspense, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, ChevronDown, Loader2, Brain, Wrench, CheckCircle2, TerminalSquare,
  ArrowDownToLine, ArrowUpFromLine, Milestone,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { TimelineItem } from '@/lib/buildTimeline';
import type { EventLogMeta, EventLogResponse, EventLogTimelineEntry } from '@/types/api';
import { useNarrationTier } from '@/hooks/useNarrationTier';
import { hasRunStats } from '@/lib/runStats';
import { RunStatChips } from './RunStatChips';
import { TOOL_RENDERERS, toolRendererFor, useRegistryEntries } from '@/platform/registries';
import { PluginBoundary } from '@/platform/PluginBoundary';

interface SourceMeta {
  /** Brand name shown verbatim (IM channels). */
  label?: string;
  /** i18n key for category sources (job / collaboration / skill). */
  labelKey?: string;
  /** Accent colour — left bar + dot + name. */
  color: string;
}

// Each source gets a distinct colour so the Inner Thoughts list is scannable.
// design_system.md §6.2 exemption: platform BRAND colours (WeChat green, Lark
// blue, ...) plus a matching series palette — centralized here on purpose;
// semantic tokens cannot express "this is Slack".
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

/**
 * Run-stats row + input/output blocks around the loop timeline. The chip row
 * itself lives in RunStatChips — the Conversation view renders the same one,
 * and two copies of "what did this turn cost" drift silently.
 */
function RunMeta({ meta, t }: { meta: EventLogMeta; t: (k: string) => string }) {
  const hasChips = hasRunStats(meta);
  if (!hasChips && !meta.input_text && !meta.final_output) return null;

  return (
    <div className="mt-2 space-y-2" data-testid="run-meta">
      <RunStatChips meta={meta} t={t} />

      {meta.input_text && (
        <div
          className="rounded-[var(--radius-md)] px-2.5 py-2"
          style={{ background: 'var(--bg-tertiary, rgba(0,0,0,0.03))' }}
        >
          <div
            className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide mb-1"
            style={{ color: 'var(--text-tertiary)' }}
          >
            <ArrowDownToLine className="w-2.5 h-2.5" />
            {t('chat.inner.meta.input')}
          </div>
          <div
            className="text-xs whitespace-pre-wrap break-words max-h-32 overflow-y-auto"
            style={{ color: 'var(--text-secondary)' }}
          >
            {meta.input_text}
          </div>
        </div>
      )}
    </div>
  );
}

/** Final-output block rendered after the loop timeline. */
function RunOutput({ meta, t }: { meta: EventLogMeta; t: (k: string) => string }) {
  if (!meta.final_output) return null;
  return (
    <div
      className="mt-2 rounded-[var(--radius-md)] px-2.5 py-2"
      style={{ background: 'var(--bg-tertiary, rgba(0,0,0,0.03))' }}
    >
      <div
        className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide mb-1"
        style={{ color: 'var(--text-tertiary)' }}
      >
        <ArrowUpFromLine className="w-2.5 h-2.5" />
        {t('chat.inner.meta.output')}
      </div>
      <div
        className="text-xs whitespace-pre-wrap break-words max-h-32 overflow-y-auto"
        style={{ color: 'var(--text-secondary)' }}
      >
        {meta.final_output}
      </div>
    </div>
  );
}

/** Stored tool_output rows often carry no tool_name; in a time-ordered log an
 *  output belongs to the nearest preceding call (same rule as segmentTurn's
 *  history conversion), which is what lets a tool renderer recognise it. */
function withOutputNames(entries: EventLogTimelineEntry[]): EventLogTimelineEntry[] {
  let lastCall = '';
  return entries.map((entry) => {
    if (entry.type === 'tool_call') lastCall = entry.tool_name || '';
    if (entry.type !== 'tool_output' || (entry.tool_name && entry.tool_name !== 'unknown')) return entry;
    return { ...entry, tool_name: lastCall };
  });
}

/** Prefer the structured timeline; fall back to legacy thinking + tool_calls. */
function toEntries(res: EventLogResponse): EventLogTimelineEntry[] {
  if (res.timeline && res.timeline.length) return withOutputNames(res.timeline);
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

/** A tool output line, or the tool's registered renderer (ui.toolRenderers);
 *  unowned, declined or crashing renderers leave the plain line. */
function ToolOutputRow({ entry }: { entry: EventLogTimelineEntry }) {
  const renderers = useRegistryEntries(TOOL_RENDERERS);
  const output = entry.content ?? entry.tool_output ?? '';
  const plain = () => (
    <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
      <TerminalSquare className="w-3 h-3 mt-0.5 shrink-0" />
      <span className="break-words font-mono">{output}</span>
    </div>
  );
  const owner = toolRendererFor(renderers, entry.tool_name ?? '', output);
  if (!owner) return plain();
  const Custom = owner.value.component;
  return (
    <PluginBoundary owner={owner.owner} fallback={plain}>
      <Suspense fallback={plain()}>
        <Custom toolName={entry.tool_name ?? ''} output={output} />
      </Suspense>
    </PluginBoundary>
  );
}

function EntryRow({
  entry,
  showNarration,
}: {
  entry: EventLogTimelineEntry;
  showNarration: boolean;
}) {
  if (entry.type === 'tool_call') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <Wrench className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        <span className="font-mono break-all">{entry.tool_name}</span>
      </div>
    );
  }
  if (entry.type === 'tool_output') return <ToolOutputRow entry={entry} />;
  if (entry.type === 'reply') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <CheckCircle2 className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--color-success)' }} />
        <span className="break-words">{entry.content}</span>
      </div>
    );
  }
  // Progress tier (design A′), same rule as TurnTimeline and ProcessEventRows.
  // This card matters MORE than either: it is the only view for turns that
  // sent no reply (background jobs, channel triggers), and those turns are
  // nothing but narration. The condition names `type === 'thinking'` because
  // this return is the fallback for every non-tool, non-reply entry, not for
  // thinking alone; the legacy `toEntries` path carries no tier, so it stays
  // receded — correctly, since it cannot know.
  const narration = showNarration && entry.type === 'thinking' && !!entry.monologue;
  return (
    <div
      className="flex items-start gap-1.5 text-xs"
      style={{ color: narration ? 'var(--text-secondary)' : 'var(--text-tertiary)' }}
    >
      {narration ? (
        <Milestone className="w-3 h-3 mt-0.5 shrink-0" />
      ) : (
        <Brain className="w-3 h-3 mt-0.5 shrink-0" />
      )}
      <span className={narration ? 'break-words' : 'break-words italic'}>{entry.content}</span>
    </div>
  );
}

export function InnerThoughtCard({ item, agentId }: { item: TimelineItem; agentId: string }) {
  const { t } = useTranslation();
  // Card level, not row level — same split as TurnTimeline (see
  // hooks/useNarrationTier): the top-level component resolves the preference,
  // shared row renderers take it as a prop.
  const showNarration = useNarrationTier();
  const [expanded, setExpanded] = useState(false);
  const [state, setState] = useState<LoadState>('idle');
  const [entries, setEntries] = useState<EventLogTimelineEntry[]>([]);
  const [runMeta, setRunMeta] = useState<EventLogMeta | null>(null);

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
        setRunMeta(res.meta ?? null);
        setState('ok');
      } catch {
        setEntries([]);
        setRunMeta(null);
        setState('error');
      }
    }
  }, [expanded, state, item.eventId, agentId]);

  return (
    <div
      data-testid="inner-thought-card"
      className="mx-3 my-1.5 rounded-[var(--radius-lg)] border pr-3 py-2 pl-3 transition-shadow hover:shadow-sm"
      style={{ borderColor: 'var(--border-subtle)', borderLeft: `3px solid ${meta.color}` }}
    >
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: meta.color }} />
        <span className="text-xs font-semibold" style={{ color: meta.color }}>{label}</span>
        <span className="text-[10px] ml-auto" style={{ color: 'var(--text-tertiary)' }}>
          {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      <div
        className={`text-xs mt-1 ${expanded ? '' : 'line-clamp-2'}`}
        style={{ color: 'var(--text-secondary)' }}
      >
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
        <div className="mt-1">
          {state === 'loading' ? (
            <div className="flex items-center gap-1.5 text-xs mt-1" style={{ color: 'var(--text-tertiary)' }}>
              <Loader2 className="w-3 h-3 animate-spin" />
              {t('chat.inner.loading')}
            </div>
          ) : state === 'error' ? (
            <div className="text-xs mt-1" style={{ color: 'var(--color-error)' }}>
              {t('chat.inner.loadFailed')}
            </div>
          ) : (
            <>
              {runMeta && <RunMeta meta={runMeta} t={t} />}
              {entries.length === 0 ? (
                <div className="text-xs mt-2" style={{ color: 'var(--text-tertiary)' }}>
                  {t('chat.inner.empty')}
                </div>
              ) : (
                <div
                  className="mt-2 space-y-1.5 pl-2 border-l-2"
                  style={{ borderColor: 'var(--border-subtle)' }}
                >
                  {entries.map((e, i) => <EntryRow key={i} entry={e} showNarration={showNarration} />)}
                </div>
              )}
              {runMeta && <RunOutput meta={runMeta} t={t} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
