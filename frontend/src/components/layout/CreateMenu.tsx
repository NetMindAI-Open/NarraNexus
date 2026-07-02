/**
 * @file_name: CreateMenu.tsx
 * @author:
 * @date: 2026-06-23
 * @description: The "+" create menu on the AGENTS section header. Splits
 * the former single create-agent button into a two-item dropdown:
 * "Create Agent" and "Create Team" — surfacing teams as a first-class
 * creatable object alongside agents (homepage's team-first model).
 *
 * Same inline-panel approach as AgentsHeaderMenu — no Radix portal, so it
 * renders correctly inside the sidebar scroll container.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Bot, Users2 } from 'lucide-react';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';

export interface CreateMenuProps {
  onCreateAgent: () => void;
  onCreateTeam: () => void;
  /** Disables the trigger while an agent is being created. */
  disabled?: boolean;
}

export function CreateMenu({ onCreateAgent, onCreateTeam, disabled }: CreateMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const handleItem = (handler: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    handler();
  };

  return (
    <div className="relative inline-flex">
      <Button
        variant="ghost"
        size="icon"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        disabled={disabled}
        className="w-7 h-7"
        title={t('layout.createMenu.create')}
        aria-label={t('layout.createMenu.createAgentOrTeam')}
      >
        <Plus className={cn('w-3.5 h-3.5', disabled && 'animate-pulse')} />
      </Button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={(e) => { e.stopPropagation(); setOpen(false); }}
          />
          <div
            className={cn(
              'absolute right-0 top-full mt-0.5 z-50',
              'min-w-[148px] py-0.5',
              'rounded-[var(--radius-sm)] border shadow-md',
              'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
            )}
          >
            <MenuItem
              icon={<Bot className="w-3 h-3" />}
              label={t('layout.createMenu.createAgent')}
              onClick={handleItem(onCreateAgent)}
            />
            <MenuItem
              icon={<Users2 className="w-3 h-3" />}
              label={t('layout.createMenu.createTeam')}
              onClick={handleItem(onCreateTeam)}
            />
          </div>
        </>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left',
        'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)] transition-colors',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
