/**
 * @file_name: BookmarkStrip.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Right-edge vertical bookmark strip — atomic-tab IA.
 *
 * Owner-decided structure: the smallest unit is an atomic tab (one tab =
 * one panel; icon + small caption so labels are readable without
 * tooltips). Categories from STRIP_CATEGORIES group the tabs with
 * horizontal mono micro-headers. Live signals from bookmarkStore render
 * as a status overlay on the owning tab: spinner (running), carbon
 * pulse + count (attention), yellow dot (info).
 *
 * 64px wide; scrolls vertically if the window is short.
 */

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Loader2 } from 'lucide-react';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import {
  STRIP_CATEGORIES,
  deriveTabStatus,
  type AtomicTabId,
  type AtomicTabDef,
  type TabStatus,
} from './tabs';
import { cn } from '@/lib/utils';

export interface BookmarkStripProps {
  agentId: string;
  activeTab: AtomicTabId | null;
  onOpen: (tab: AtomicTabId) => void;
}

export function BookmarkStrip({ agentId, activeTab, onOpen }: BookmarkStripProps) {
  const agentState = useBookmarkStore((s) => s.agents[agentId]);

  return (
    <div
      className="flex flex-col w-16 shrink-0 select-none overflow-y-auto overflow-x-hidden py-1"
      style={{ borderLeft: '1px solid var(--nm-hairline)', scrollbarWidth: 'none' }}
      data-help-id="bookmarks.strip"
    >
      {STRIP_CATEGORIES.map((category, ci) => (
        <div key={category.label} className="flex flex-col items-stretch">
          {/* Category header — horizontal mono micro-label */}
          <div
            className={cn(
              'px-2 pt-2 pb-1',
              ci > 0 && 'mt-1.5 border-t border-[var(--nm-hairline)]',
            )}
            aria-hidden
          >
            <span
              className="block text-[8px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em] leading-none whitespace-nowrap"
              style={{ color: 'var(--nm-ink30)' }}
            >
              {category.label}
            </span>
          </div>

          {category.tabs.map((tab) => (
            <AtomicTab
              key={tab.id}
              tab={tab}
              active={activeTab === tab.id}
              status={deriveTabStatus(agentState, tab.id)}
              onOpen={onOpen}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AtomicTab
// ---------------------------------------------------------------------------

interface AtomicTabProps {
  tab: AtomicTabDef;
  active: boolean;
  status: TabStatus;
  onOpen: (tab: AtomicTabId) => void;
}

function AtomicTab({ tab, active, status, onOpen }: AtomicTabProps) {
  const Icon = tab.icon;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={tab.label}
            aria-expanded={active}
            data-help-id={`bookmarks.${tab.id}`}
            onClick={() => onOpen(tab.id)}
            className={cn(
              'relative flex flex-col items-center justify-center gap-0.5 h-11 w-full shrink-0',
              'cursor-pointer transition-colors duration-150',
              'hover:bg-[var(--nm-paper-warm)]',
              active && 'bg-[var(--nm-paper-warm)]',
            )}
            style={
              // Bookmark tongue: the active tab shows a 2px ink rule on the
              // outer edge — the bit of the bookmark you can see from the
              // page edge.
              active ? { boxShadow: 'inset -2px 0 0 var(--text-primary)' } : undefined
            }
          >
            <Icon
              className={cn('w-4 h-4', status.status === 'attention' && 'animate-pulse')}
              style={{
                color:
                  status.status === 'attention'
                    ? 'var(--color-carbon)'
                    : active
                      ? 'var(--text-primary)'
                      : 'var(--text-tertiary)',
              }}
              aria-hidden
            />

            {/* Status overlay — top-right corner of the tab */}
            {status.status === 'running' && (
              <Loader2
                className="absolute top-1 right-2 w-2.5 h-2.5 animate-spin"
                style={{ color: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}
            {status.status === 'attention' && status.badge !== undefined && status.badge > 0 && (
              <span
                aria-live="polite"
                className="absolute top-0.5 right-1.5 flex items-center justify-center min-w-[13px] h-[13px] px-0.5 rounded-full text-[8px] font-bold tabular-nums leading-none"
                style={{ background: 'var(--color-carbon)', color: 'var(--color-gray-50)' }}
              >
                {status.badge > 99 ? '99+' : status.badge}
              </span>
            )}
            {status.status === 'info' && (
              <span
                className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full allow-circle"
                style={{ background: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}

            {/* Caption — readable without a tooltip */}
            <span
              className="block max-w-full px-0.5 truncate text-[8px] font-[family-name:var(--font-mono)] uppercase tracking-[0.06em] leading-none"
              style={{
                color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
              }}
            >
              {tab.stripLabel ?? tab.label}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">{tab.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
