/**
 * @file_name: ContextPanelHeader.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Context Panel Header - Bioluminescent Terminal style
 *
 * Tab options:
 * - Runtime: Execution steps / Narrative history
 * - Awareness: Self-awareness / Social network
 * - Agent Inbox: Messages received by the agent
 * - Jobs: Task management
 *
 * The settings button opens a full-screen Settings modal (replaces the old popover).
 */

import { useState } from 'react';
import { Activity, Settings, Inbox, ListTodo, Puzzle, Cpu } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui';
import { CostPopover } from '@/components/cost/CostPopover';
import { SettingsModal } from '@/components/settings/SettingsModal';
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

  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="flex items-center justify-between mb-3 px-1">
      {/* Tab Buttons */}
      <Tabs value={activeTab} onValueChange={(value) => onTabChange(value as ContextTab)}>
        <TabsList className="bg-[var(--bg-secondary)] border border-[var(--border-default)] p-1 rounded-xl gap-0.5">
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
                  'relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg',
                  'text-xs font-medium cursor-pointer select-none',
                  'transition-all duration-200',
                  isActive && [
                    'bg-[var(--bg-elevated)]',
                    'text-[var(--accent-primary)]',
                    'border border-[var(--accent-primary)]/20',
                  ],
                  !isActive && [
                    'text-[var(--text-tertiary)]',
                    'border border-transparent',
                    'hover:text-[var(--text-secondary)]',
                    'hover:bg-[var(--bg-tertiary)]',
                  ],
                )}
              >
                <Icon className="w-3.5 h-3.5 shrink-0" />
                <span>{tab.label}</span>

                {/* Notification badge */}
                {hasNotification && (
                  <span className="h-4 min-w-4 px-1 flex items-center justify-center text-[9px] font-bold bg-[var(--color-error)] text-white rounded-full">
                    {agentInboxUnreadCount > 9 ? '9+' : agentInboxUnreadCount}
                  </span>
                )}

                {/* Awareness update red dot */}
                {hasAwarenessDot && (
                  <span className="w-2 h-2 rounded-full bg-red-500 shrink-0 animate-pulse" />
                )}
              </TabsTrigger>
            );
          })}
        </TabsList>
      </Tabs>

      {/* Utility buttons */}
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          title="Settings"
          onClick={() => setSettingsOpen(true)}
        >
          <Cpu className="w-5 h-5" />
        </Button>
        <CostPopover />
      </div>

      {/* Settings Modal */}
      <SettingsModal isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
