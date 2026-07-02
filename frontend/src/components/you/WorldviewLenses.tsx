/**
 * @file_name: WorldviewLenses.tsx
 * @author:
 * @date: 2026-06-24
 * @description: The Worldview tab of the "You" workspace — how each of the
 * user's agents sees them, plus a glimpse of each agent's own worldview. The
 * two halves that compose "your world": every agent's view of YOU (carbon) +
 * that agent's model of the world (silicon).
 *
 * Layout (redesigned 2026-06-24): an overall summary on top, then ONE collapsed
 * row per agent — click to expand its full view-of-you + worldview. Collapsing
 * by default keeps the tab scannable as the agent count grows.
 *
 * Data: api.getMyWorldview() → GET /api/me/worldview. Owner-scoped: never reads
 * the selected agentId.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import { api } from '@/lib/api';
import type { MyWorldviewLens } from '@/types';
import { BracketEmptyState } from '@/components/nm';
import { cn } from '@/lib/utils';

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; items: MyWorldviewLens[] };

export function WorldviewLenses({ search = '' }: { search?: string }) {
  const { t } = useTranslation();
  const q = search.trim().toLowerCase();
  const [state, setState] = useState<LoadState>({ phase: 'loading' });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    api
      .getMyWorldview()
      .then((res) => {
        if (!alive) return;
        if (res.success) setState({ phase: 'ready', items: res.lenses });
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

  const lenses = useMemo(() => {
    if (state.phase !== 'ready') return [];
    return state.items.filter(
      (l) =>
        !q ||
        l.agent_name.toLowerCase().includes(q) ||
        l.sees_you.toLowerCase().includes(q) ||
        l.worldview.some((w) => w.toLowerCase().includes(q)),
    );
  }, [state, q]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (state.phase === 'loading') {
    return (
      <Center>
        <span className="text-[12px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)] animate-pulse">
          {t('you.worldview.loading')}
        </span>
      </Center>
    );
  }
  if (state.phase === 'error') {
    return (
      <Center>
        <BracketEmptyState label={t('you.worldview.errorLabel')} hint={state.message} />
      </Center>
    );
  }
  if (lenses.length === 0) {
    return (
      <Center>
        <BracketEmptyState
          label={q ? t('you.worldview.noMatches') : t('you.worldview.emptyLabel')}
          hint={
            q
              ? t('you.worldview.noMatchesHint', { query: search.trim() })
              : t('you.worldview.emptyHint')
          }
        />
      </Center>
    );
  }

  return (
    <div className="w-full h-full flex flex-col p-5">
      {/* Summary */}
      <div className="shrink-0 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--bg-primary)] p-3.5 mb-4">
        <div className="flex items-center gap-2 mb-1.5">
          <BindingDots />
          <span className="text-[13px] font-medium text-[var(--text-primary)]">
            {t('you.worldview.summaryTitle')}
          </span>
        </div>
        <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
          {lenses.length === 1
            ? t('you.worldview.summaryBody', { count: lenses.length })
            : t('you.worldview.summaryBodyPlural', { count: lenses.length })}
        </p>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {lenses.map((l) => (
            <button
              key={l.agent_id}
              type="button"
              onClick={() => {
                setExpanded(new Set([l.agent_id]));
                document.getElementById(`wv-${l.agent_id}`)?.scrollIntoView({ block: 'nearest' });
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--nm-hairline)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--color-silicon)] hover:text-[var(--text-primary)] transition-colors"
            >
              <span className="w-1.5 h-1.5 rounded-full allow-circle" style={{ background: 'var(--color-silicon)' }} aria-hidden />
              {l.agent_name}
            </button>
          ))}
        </div>
      </div>

      {/* Collapsible lenses */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 space-y-1.5">
        {lenses.map((l) => {
          const open = expanded.has(l.agent_id);
          return (
            <div
              key={l.agent_id}
              id={`wv-${l.agent_id}`}
              className="rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--bg-primary)] overflow-hidden"
            >
              {/* Row — click to expand */}
              <button
                type="button"
                onClick={() => toggle(l.agent_id)}
                aria-expanded={open}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
              >
                <ChevronRight
                  className={cn('w-3.5 h-3.5 shrink-0 text-[var(--text-tertiary)] transition-transform', open && 'rotate-90')}
                  aria-hidden
                />
                <span className="w-1.5 h-1.5 rounded-full allow-circle shrink-0" style={{ background: 'var(--color-silicon)' }} aria-hidden />
                <span className="text-[13px] font-medium text-[var(--text-primary)] shrink-0">{l.agent_name}</span>
                {!open && (
                  <span className="min-w-0 truncate text-[12px] text-[var(--text-tertiary)]">
                    {l.sees_you}
                  </span>
                )}
              </button>

              {/* Expanded detail */}
              {open && (
                <div className="px-3 pb-3 pl-[2.1rem]">
                  <div className="mb-1 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.13em] text-[var(--color-carbon)]">
                    {t('you.worldview.seesYouAs')}
                  </div>
                  <p className="text-[12.5px] leading-relaxed text-[var(--text-primary)] mb-2.5">{l.sees_you}</p>

                  {l.worldview.length > 0 && (
                    <>
                      <div className="mb-1 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.13em] text-[var(--color-silicon)]">
                        {t('you.worldview.itsWorldview')}
                      </div>
                      <ul className="space-y-1">
                        {l.worldview.map((w, i) => (
                          <li key={i} className="flex gap-1.5 text-[12px] leading-snug text-[var(--text-secondary)]">
                            <span className="shrink-0 text-[var(--text-tertiary)]">·</span>
                            <span>{w}</span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BindingDots() {
  return (
    <span className="inline-flex items-center gap-1 shrink-0" aria-hidden>
      <span className="w-[6px] h-[6px] rounded-full allow-circle" style={{ background: 'var(--color-carbon)' }} />
      <span className="w-2.5 h-px" style={{ background: 'var(--rule)' }} />
      <span className="w-[6px] h-[6px] rounded-full allow-circle" style={{ background: 'var(--color-silicon)' }} />
    </span>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="w-full h-full flex items-center justify-center p-8">{children}</div>;
}

export default WorldviewLenses;
