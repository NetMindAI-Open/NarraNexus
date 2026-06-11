/**
 * @file_name: AgentRowMenu.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Kebab (⋮) context menu for a single agent row. Exposes
 * rename, delete, and optional public/private toggle. Extracted from the
 * inline action buttons in AgentList so the row markup stays clean and the
 * menu entries can be tested independently.
 */

import { useState } from 'react';
import { MoreVertical, Pencil, Trash2, Globe, Lock } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface AgentRowMenuProps {
  agentId: string;
  agentName: string;
  isOwner: boolean;
  isPublic: boolean;
  showPublicToggle: boolean;
  onStartEdit: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  onTogglePublic: (e: React.MouseEvent) => void;
}

/**
 * Kebab menu (⋮) attached to each agent row.
 *
 * Opens inline as an absolute-positioned panel — no Radix Popover — so it
 * works inside the sidebar scroll container without portal clipping.
 *
 * Only owner-specific actions (delete, public toggle) are hidden when
 * isOwner=false.  The rename action is always shown because any user can
 * rename an agent they can see (backend enforces real ownership).
 */
export function AgentRowMenu({
  isOwner,
  isPublic,
  showPublicToggle,
  onStartEdit,
  onDelete,
  onTogglePublic,
}: AgentRowMenuProps) {
  const [open, setOpen] = useState(false);

  const handleTrigger = (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen((v) => !v);
  };

  const handleItem = (handler: (e: React.MouseEvent) => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    handler(e);
  };

  return (
    <div className="relative inline-flex" onClick={(e) => e.stopPropagation()}>
      <button
        aria-label="Agent options"
        onClick={handleTrigger}
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
          {/* Click-outside overlay */}
          <div
            className="fixed inset-0 z-40"
            onClick={(e) => { e.stopPropagation(); setOpen(false); }}
          />
          <div
            className={cn(
              'absolute right-0 top-full mt-0.5 z-50',
              'min-w-[120px] py-0.5',
              'rounded-[var(--radius-sm)] border shadow-md',
              'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
            )}
          >
            {/* Rename — available to everyone */}
            <MenuItem
              icon={<Pencil className="w-3 h-3" />}
              label="Rename"
              onClick={handleItem(onStartEdit)}
            />

            {/* Owner-only: public/private toggle */}
            {showPublicToggle && isOwner && (
              <MenuItem
                icon={
                  isPublic
                    ? <Globe className="w-3 h-3" />
                    : <Lock className="w-3 h-3" />
                }
                label={isPublic ? 'Set to Private' : 'Set to Public'}
                onClick={handleItem(onTogglePublic)}
              />
            )}

            {/* Owner-only: delete */}
            {isOwner && (
              <MenuItem
                icon={<Trash2 className="w-3 h-3" />}
                label="Delete"
                danger
                onClick={handleItem(onDelete)}
              />
            )}
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
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left',
        'transition-colors',
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
