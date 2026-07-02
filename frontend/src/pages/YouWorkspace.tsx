/**
 * @file_name: YouWorkspace.tsx
 * @author:
 * @date: 2026-06-23
 * @description: The "You" workspace — the owner-scoped counterpart to the
 * agent-scoped right rail.
 *
 * Reached by clicking your own avatar in the left sidebar. Where the right
 * rail is headed by "<agent>" and shows ONE agent's Config / Memory / Network,
 * this page is headed by "You · <name>" and aggregates ACROSS all your agents:
 *   - Memory  — your storylines/narratives, by topic (carbon · Narra)
 *   - Network — everyone your agents know, combined (silicon · Nexus)
 *   - World   — what your agents collectively believe about you
 * plus a Notes scratchpad — your own (carbon) input, the human half of the
 * carbon·silicon pair.
 *
 * Phase 1 (this file): the shell — entry, header, tab switching, and a working
 * local Notes pad. The three visualization tabs render honest empty states;
 * each is wired to its real cross-agent data source in a following change
 * (Memory first). NO fabricated data is shown in the product.
 */
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Search } from 'lucide-react';
import { useConfigStore } from '@/stores/configStore';
import { RingAvatar } from '@/components/nm';
import { NarraMemoryTimeline } from '@/components/you/NarraMemoryTimeline';
import { NexusNetworkGraph } from '@/components/you/NexusNetworkGraph';
import { WorldviewLenses } from '@/components/you/WorldviewLenses';
import { cn } from '@/lib/utils';

type YouTab = 'memory' | 'network' | 'world';

interface TabDef {
  id: YouTab;
  labelKey: string;
}

// Each tab now owns its own data view + empty state (NarraMemoryTimeline,
// NexusNetworkGraph, WorldviewLenses).
const TABS: TabDef[] = [
  { id: 'memory', labelKey: 'pages.you.tabMemory' },
  { id: 'network', labelKey: 'pages.you.tabNetwork' },
  { id: 'world', labelKey: 'pages.you.tabWorld' },
];

export function YouWorkspace() {
  const { t } = useTranslation();
  const userId = useConfigStore((s) => s.userId);
  const displayName = useConfigStore((s) => s.displayName);
  const name = (displayName || userId || t('pages.you.you')).trim();

  const [tab, setTab] = useState<YouTab>('memory');

  // Notes — a personal scratchpad. Persisted locally per user for now; a later
  // change adds an explicit "save as memory" that writes a memory_observation
  // your agents can read.
  const notesKey = `narranexus:you-notes:${userId ?? 'anon'}`;
  const [notes, setNotes] = useState('');
  useEffect(() => {
    try {
      setNotes(localStorage.getItem(notesKey) ?? '');
    } catch {
      /* localStorage unavailable — keep the in-memory draft */
    }
  }, [notesKey]);
  const onNotesChange = useCallback(
    (value: string) => {
      setNotes(value);
      try {
        localStorage.setItem(notesKey, value);
      } catch {
        /* ignore persistence failure; the draft still lives in state */
      }
    },
    [notesKey],
  );

  // Search filters the active visualization (Memory storylines / Network
  // entities). Cleared on tab switch so a stale query can't hide everything.
  const [query, setQuery] = useState('');
  const switchTab = (id: YouTab) => {
    setTab(id);
    setQuery('');
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[860px] min-h-full px-6 py-8 flex flex-col">
        {/* Header — "You · <name>", carbon·silicon binding dots. */}
        <div className="flex items-center gap-3 mb-1 shrink-0">
          <RingAvatar species="carbon" label={name || '?'} size="md" />
          <div className="flex-1 min-w-0">
            <h1 className="text-[18px] font-medium leading-tight text-[var(--text-primary)]">
              {t('pages.you.you')}{name ? ` · ${name}` : ''}
            </h1>
            <p className="mt-0.5 text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
              {t('pages.you.subtitle')}
            </p>
          </div>
          <BindingDots />
        </div>

        {/* Tabs */}
        <div
          className="flex items-center gap-6 mt-6 border-b border-[var(--rule)] shrink-0"
          role="tablist"
          aria-label={t('pages.you.viewsAriaLabel')}
        >
          {TABS.map((tabDef) => {
            const on = tabDef.id === tab;
            return (
              <button
                key={tabDef.id}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => switchTab(tabDef.id)}
                className={cn(
                  'relative -mb-px py-2 text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] transition-colors',
                  on
                    ? 'text-[var(--color-carbon)]'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                )}
              >
                {t(tabDef.labelKey)}
                {on && (
                  <span
                    className="absolute inset-x-0 -bottom-px h-0.5"
                    style={{ background: 'var(--color-carbon)' }}
                    aria-hidden
                  />
                )}
              </button>
            );
          })}

          {/* Search — filters the active visualization. */}
          <div className="ml-auto mb-1.5 flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--bg-primary)] px-2 py-1">
            <Search className="w-3 h-3 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('pages.you.searchPlaceholder')}
              aria-label={t('pages.you.searchAriaLabel')}
              spellCheck={false}
              className="w-28 sm:w-40 bg-transparent text-[12px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
            />
          </div>
        </div>

        {/* Panel — the visualization gets the bulk of the height. Each tab
            owns its own layout; the empty-state tabs center their content. */}
        <div
          role="tabpanel"
          className="mt-5 flex-1 min-h-[340px] rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--bg-secondary)] overflow-hidden flex"
        >
          {tab === 'memory' ? (
            <NarraMemoryTimeline search={query} />
          ) : tab === 'network' ? (
            <NexusNetworkGraph search={query} />
          ) : (
            <WorldviewLenses search={query} />
          )}
        </div>

        {/* Notes — the human (carbon) half. Compact, docked at the bottom. */}
        <div className="mt-6 shrink-0">
          <div className="flex items-center gap-2 mb-2">
            <span
              className="w-1.5 h-1.5 rounded-full allow-circle"
              style={{ background: 'var(--color-carbon)' }}
              aria-hidden
            />
            <span className="text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
              {t('pages.you.notesLabel')}
            </span>
            <span className="ml-auto text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
              {t('pages.you.savedOnDevice')}
            </span>
          </div>
          <textarea
            value={notes}
            onChange={(e) => onNotesChange(e.target.value)}
            placeholder={t('pages.you.notesPlaceholder')}
            spellCheck={false}
            className={cn(
              'w-full min-h-[72px] resize-y rounded-[var(--radius-md)] p-3',
              'border border-[var(--nm-hairline)] bg-[var(--bg-primary)]',
              'text-[13px] leading-relaxed text-[var(--text-primary)]',
              'outline-none focus:border-[var(--color-carbon)] transition-colors',
              'placeholder:text-[var(--text-tertiary)]',
            )}
          />
        </div>
      </div>
    </div>
  );
}

/** Carbon·silicon binding-dot motif: you (human) + agent (machine). */
function BindingDots() {
  return (
    <span className="inline-flex items-center gap-1.5 shrink-0" aria-hidden>
      <span className="w-[7px] h-[7px] rounded-full allow-circle" style={{ background: 'var(--color-carbon)' }} />
      <span className="w-3.5 h-px" style={{ background: 'var(--rule)' }} />
      <span className="w-[7px] h-[7px] rounded-full allow-circle" style={{ background: 'var(--color-silicon)' }} />
    </span>
  );
}

export default YouWorkspace;
