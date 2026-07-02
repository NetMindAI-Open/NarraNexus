/**
 * @file_name: TeamRowMenu.tsx
 * @author:
 * @date: 2026-06-23
 * @description: Kebab (⋮) context menu for the team group-chat row — mirrors
 * AgentRowMenu so a team's row offers the same Rename / Delete affordances as
 * an agent row. Inline absolute panel (no portal) so it works inside the
 * sidebar scroll container.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MoreVertical, Pencil, Trash2, UserPlus } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface TeamRowMenuProps {
  /** Create a new agent already assigned to this team (#43). The old
   *  AgentGroupSection-header "+" no longer exists in the TEAMS-row layout,
   *  so this capability is re-homed into the row's ⋮ menu. */
  onAddAgent: (e: React.MouseEvent) => void;
  /** True while an agent create is in flight — disables the Add-agent item. */
  addingAgent?: boolean;
  onRename: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  /** Fired on open/close so the host row can lift its z-index above the rows
   *  below (each row is its own stacking context). */
  onOpenChange?: (open: boolean) => void;
}

export function TeamRowMenu({ onAddAgent, addingAgent, onRename, onDelete, onOpenChange }: TeamRowMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Notify the parent from the event handler (NOT inside a setState updater —
  // that runs during render and triggers a cross-component setState warning).
  const setOpenAndNotify = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };

  const handleItem = (handler: (e: React.MouseEvent) => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenAndNotify(false);
    handler(e);
  };

  return (
    <div className="relative inline-flex" onClick={(e) => e.stopPropagation()}>
      <button
        aria-label={t('layout.teamRowMenu.options')}
        onClick={(e) => { e.stopPropagation(); setOpenAndNotify(!open); }}
        className={cn(
          'p-1 rounded-[var(--radius-xs)] transition-colors',
          'hover:bg-[var(--nm-paper-warm)]',
          open && 'bg-[var(--nm-paper-warm)]',
        )}
      >
        <MoreVertical className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={(e) => { e.stopPropagation(); setOpenAndNotify(false); }}
          />
          <div
            className={cn(
              'absolute right-0 top-full mt-0.5 z-50',
              'min-w-[120px] py-0.5',
              'rounded-[var(--radius-sm)] border shadow-md',
              'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
            )}
          >
            <MenuItem
              icon={<UserPlus className="w-3 h-3" />}
              label={addingAgent ? 'Adding…' : 'Add agent'}
              disabled={addingAgent}
              onClick={handleItem(onAddAgent)}
            />
            <MenuItem
              icon={<Pencil className="w-3 h-3" />}
              label={t('layout.teamRowMenu.rename')}
              onClick={handleItem(onRename)}
            />
            <MenuItem
              icon={<Trash2 className="w-3 h-3" />}
              label={t('layout.teamRowMenu.delete')}
              danger
              onClick={handleItem(onDelete)}
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
  danger,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors',
        disabled && 'opacity-50 cursor-not-allowed',
        danger
          ? 'text-[var(--color-error)] hover:bg-[var(--color-error)]/10'
          : 'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)]',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
