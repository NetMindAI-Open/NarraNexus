/**
 * @file_name: NarraMemoryTimeline.tsx
 * @author:
 * @date: 2026-06-23
 * @description: The Narra Memory tab of the "You" workspace — a real-data
 * timeline of the user's lived storylines (narratives) across ALL their
 * agents.
 *
 * Each narrative is one lane: a carbon bar spanning created_at → updated_at on
 * a shared time axis, labelled by storyline name + owning agent. Point-in-time
 * narratives (created == updated) render as a minimum-width marker. Clicking a
 * lane reveals its topic + summary. Data: api.getMyNarratives() →
 * GET /api/me/narratives (seeded scaffold narratives excluded server-side).
 *
 * This is owner-scoped: it never reads the selected agentId.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import type { MyNarrative } from '@/types';
import { BracketEmptyState } from '@/components/nm';
import { cn } from '@/lib/utils';

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; items: MyNarrative[] };

const fmtDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });

function ts(value: string | null): number | null {
  if (!value) return null;
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : t;
}

export function NarraMemoryTimeline({ search = '' }: { search?: string }) {
  const { t } = useTranslation();
  const q = search.trim().toLowerCase();
  const [state, setState] = useState<LoadState>({ phase: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getMyNarratives()
      .then((res) => {
        if (!alive) return;
        if (res.success) setState({ phase: 'ready', items: res.narratives });
        else setState({ phase: 'error', message: res.error || t('you.common.failedToLoad') });
      })
      .catch((e: unknown) => {
        if (alive) setState({ phase: 'error', message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      alive = false;
    };
    // Load once on mount; `t` is referentially stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Time range + lane layout, derived once per data set. A search query filters
  // storylines by name / summary / topic / owning agent.
  const layout = useMemo(() => {
    if (state.phase !== 'ready') return null;
    const items = state.items.filter(
      (n) =>
        !q ||
        n.name.toLowerCase().includes(q) ||
        n.summary.toLowerCase().includes(q) ||
        n.topic_hint.toLowerCase().includes(q) ||
        n.agent_name.toLowerCase().includes(q),
    );
    if (items.length === 0) return null;
    const now = Date.now();
    const stamps = items
      .flatMap((n) => [ts(n.created_at), ts(n.updated_at)])
      .filter((t): t is number => t !== null);
    const min = stamps.length ? Math.min(...stamps) : now;
    // Pad the left edge a touch so the earliest bar isn't flush to 0%.
    const start = min - (now - min) * 0.04 - 1;
    const span = Math.max(now - start, 1);
    const pct = (t: number) => ((t - start) / span) * 100;

    const lanes = [...items]
      .sort((a, b) => (ts(a.created_at) ?? 0) - (ts(b.created_at) ?? 0))
      .map((n) => {
        const c = ts(n.created_at) ?? now;
        const u = Math.max(ts(n.updated_at) ?? c, c);
        const left = pct(c);
        const width = Math.max(pct(u) - left, 3); // min marker width
        return { n, left, width };
      });

    // 4 evenly spaced axis ticks.
    const ticks = Array.from({ length: 4 }, (_, i) => {
      const t = start + (span * (i + 0.5)) / 4;
      return { left: ((t - start) / span) * 100, label: fmtDay.format(new Date(t)) };
    });

    return { lanes, ticks };
  }, [state, q]);

  if (state.phase === 'loading') {
    return (
      <Center>
        <span className="text-[12px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)] animate-pulse">
          {t('you.memory.loading')}
        </span>
      </Center>
    );
  }
  if (state.phase === 'error') {
    return (
      <Center>
        <BracketEmptyState label={t('you.memory.errorLabel')} hint={state.message} />
      </Center>
    );
  }
  if (!layout) {
    return (
      <Center>
        <BracketEmptyState
          label={q ? t('you.memory.noMatches') : t('you.memory.emptyLabel')}
          hint={
            q
              ? t('you.memory.noMatchesHint', { query: search.trim() })
              : t('you.memory.emptyHint')
          }
        />
      </Center>
    );
  }

  const selectedItem = state.items.find((n) => n.narrative_id === selected) ?? null;

  return (
    <div className="w-full h-full flex flex-col p-5">
      <style>{`
        .nm-storybar{transition:box-shadow .15s,filter .15s}
        .group:hover .nm-storybar{filter:drop-shadow(0 0 5px var(--color-carbon))}
      `}</style>
      {/* Header */}
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
          {layout.lanes.length === 1
            ? t('you.memory.storylineCount', { count: layout.lanes.length })
            : t('you.memory.storylineCountPlural', { count: layout.lanes.length })}
        </span>
        <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          {t('you.memory.byTopic')}
        </span>
      </div>

      {/* Lanes */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1">
        {layout.lanes.map(({ n, left, width }) => {
          const on = n.narrative_id === selected;
          const label = n.name || n.topic_hint || n.narrative_id;
          return (
            <button
              key={n.narrative_id}
              type="button"
              onClick={() => setSelected(on ? null : n.narrative_id)}
              className="group w-full flex items-center gap-3 py-1.5 text-left"
            >
              {/* Lane label gutter */}
              <div className="w-[190px] shrink-0 min-w-0 flex items-center gap-1.5">
                <span
                  className="w-1.5 h-1.5 shrink-0 rounded-full allow-circle"
                  style={{ background: 'var(--color-silicon)' }}
                  title={n.agent_name}
                  aria-hidden
                />
                <span
                  className={cn(
                    'truncate text-[12px] leading-tight',
                    on ? 'text-[var(--color-carbon)] font-medium' : 'text-[var(--text-primary)]',
                  )}
                  title={`${label} · ${n.agent_name}`}
                >
                  {label}
                </span>
              </div>
              {/* Track */}
              <div className="relative flex-1 h-5">
                <div
                  className="absolute inset-y-0 left-0 right-0 my-auto h-px"
                  style={{ background: 'var(--nm-hairline)' }}
                  aria-hidden
                />
                <div
                  className="nm-storybar absolute top-1/2 -translate-y-1/2 h-2.5 rounded-full allow-circle"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: 'var(--color-carbon)',
                    // A soft carbon glow at rest signals "clickable"; the selected
                    // bar swaps to a ring + a stronger glow. Hover brightening is
                    // a CSS `filter` (see <style> below) so it stacks on top.
                    boxShadow: on
                      ? '0 0 0 2px var(--bg-secondary), 0 0 0 3px var(--color-carbon), 0 0 12px 0 var(--color-carbon)'
                      : '0 0 7px -1px var(--color-carbon)',
                  }}
                />
              </div>
            </button>
          );
        })}
      </div>

      {/* Time axis */}
      <div className="relative h-5 mt-1 ml-[202px] shrink-0">
        <div className="absolute inset-x-0 top-0 h-px" style={{ background: 'var(--nm-hairline)' }} aria-hidden />
        {layout.ticks.map((tk, i) => (
          <span
            key={i}
            className="absolute top-1 -translate-x-1/2 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)] whitespace-nowrap"
            style={{ left: `${tk.left}%` }}
          >
            {tk.label}
          </span>
        ))}
        <span className="absolute top-1 right-0 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--color-carbon)]">
          {t('you.memory.now')}
        </span>
      </div>

      {/* Selected detail */}
      {selectedItem && (
        <div className="mt-3 shrink-0 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--bg-primary)] p-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[13px] font-medium text-[var(--text-primary)] truncate">
              {selectedItem.name || selectedItem.topic_hint || t('you.memory.untitledStoryline')}
            </span>
            <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)] shrink-0">
              <span className="w-1.5 h-1.5 rounded-full allow-circle" style={{ background: 'var(--color-silicon)' }} aria-hidden />
              {selectedItem.agent_name}
            </span>
          </div>
          {selectedItem.topic_hint && selectedItem.topic_hint !== selectedItem.name && (
            <p className="text-[12px] text-[var(--text-secondary)] mb-1">{selectedItem.topic_hint}</p>
          )}
          {selectedItem.summary && (
            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">{selectedItem.summary}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            <span>{t('you.memory.started', { date: selectedItem.created_at ? fmtDay.format(new Date(selectedItem.created_at)) : '—' })}</span>
            <span>{t('you.memory.lastActive', { date: selectedItem.updated_at ? fmtDay.format(new Date(selectedItem.updated_at)) : '—' })}</span>
            {selectedItem.round_counter > 0 && <span>{t('you.memory.rounds', { count: selectedItem.round_counter })}</span>}
            {selectedItem.topic_keywords.length > 0 && (
              <span className="normal-case tracking-normal">{selectedItem.topic_keywords.slice(0, 5).join(' · ')}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="w-full h-full flex items-center justify-center p-8">{children}</div>;
}

export default NarraMemoryTimeline;
