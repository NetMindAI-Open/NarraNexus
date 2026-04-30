/**
 * @file_name: ContextPanelHeader.tsx
 * @description: Context panel tab bar — archive style.
 *   Flat underline tabs in a single hairline-bounded row. No pills,
 *   no filled backgrounds, no rounded container. Notification / dot
 *   indicators sit inline with the label.
 *
 *   Layout invariants:
 *   - CostPopover always stays pinned to the right edge (shrink-0 + pr-1 so
 *     its -top-1 -right-1 unread badge never overflows the panel frame).
 *   - Tab row scrolls horizontally when the panel is too narrow, so tabs
 *     never collide with the cost indicator. Scrollbar is hidden visually.
 */

import { Activity, Settings, Inbox, ListTodo, Puzzle } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui';
import { CostPopover } from '@/components/cost/CostPopover';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn } from '@/lib/utils';

export type ContextTab = 'runtime' | 'awareness' | 'inbox' | 'jobs' | 'skills';

interface ContextPanelHeaderProps {
  activeTab: ContextTab;
  onTabChange: (tab: ContextTab) => void;
}

const tabs: { id: ContextTab; icon: typeof Activity; label: string }[] = [
  { id: 'runtime', icon: Activity, label: 'Runtime' },
  { id: 'awareness', icon: Settings, label: 'Config' },
  { id: 'inbox', icon: Inbox, label: 'Inbox' },
  { id: 'jobs', icon: ListTodo, label: 'Jobs' },
  { id: 'skills', icon: Puzzle, label: 'Skills' },
];

export function ContextPanelHeader({ activeTab, onTabChange }: ContextPanelHeaderProps) {
  const { agentInboxUnreadCount } = usePreloadStore();
  const { agentId, awarenessUpdatedAgents } = useConfigStore();
  const hasAwarenessUpdate = awarenessUpdatedAgents.includes(agentId);

  return (
    <div className="flex items-end justify-between gap-2 min-w-0">
      {/* Tab row — scrolls horizontally when too narrow, hidden scrollbar.
          Uses ScrollArea (hideScrollbar) so the row scrolls but no track
          appears, even on macOS "always show scrollbars". */}
      <ScrollArea horizontal hideScrollbar className="flex-1 min-w-0">
        <Tabs value={activeTab} onValueChange={(value) => onTabChange(value as ContextTab)}>
          <TabsList className="ctx-tabs flex w-max gap-0">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              const hasNotification = tab.id === 'inbox' && agentInboxUnreadCount > 0;
              const hasAwarenessDot = tab.id === 'awareness' && hasAwarenessUpdate;

              return (
                <TabsTrigger
                  key={tab.id}
                  value={tab.id}
                  className={cn(
                    'relative flex items-center gap-1.5 px-2.5 py-2 shrink-0',
                    'text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em]',
                    'border-b-2 transition-colors duration-150',
                    'cursor-pointer select-none whitespace-nowrap',
                    isActive
                      ? 'border-[var(--text-primary)] text-[var(--text-primary)]'
                      : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                  )}
                >
                  <Icon className="w-3 h-3 shrink-0" />
                  <span>{tab.label}</span>

                  {hasNotification && (
                    <span className="text-[9px] tabular-nums normal-case tracking-normal text-[var(--color-yellow-500)]">
                      · {agentInboxUnreadCount > 9 ? '9+' : agentInboxUnreadCount}
                    </span>
                  )}
                  {hasAwarenessDot && (
                    <span className="w-1 h-1 rounded-full allow-circle bg-[var(--color-yellow-500)] animate-pulse shrink-0 ml-0.5" />
                  )}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </Tabs>
      </ScrollArea>

      {/* Cost indicator — always pinned to the right, pr-1 leaves room for
          its -right-1 unread-badge overhang without clipping. */}
      <div className="flex items-center gap-1 shrink-0 pr-1">
        <CostPopover />
      </div>
    </div>
  );
}
