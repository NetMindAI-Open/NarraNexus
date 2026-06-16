/**
 * @file_name: AgentsHeaderMenu.tsx
 * @author:
 * @date: 2026-06-10
 * @description: The ⋯ (more) menu on the AGENTS section header. Exposes
 * import, export, and manage-teams actions — items that are infrequent
 * enough to move off the primary toolbar into a compact overflow menu —
 * the single entry point for team management (spec §11.2).
 */

import { useState } from 'react';
import { MoreHorizontal, Upload, Package, Users2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface AgentsHeaderMenuProps {
  onImport: () => void;
  onExport: () => void;
  onManageTeams: () => void;
}

/**
 * Overflow ⋯ menu for the AGENTS section header.
 *
 * Same inline-panel approach as AgentRowMenu — no Radix portal, so it
 * renders correctly inside the sidebar scroll container.
 */
export function AgentsHeaderMenu({
  onImport,
  onExport,
  onManageTeams,
}: AgentsHeaderMenuProps) {
  const [open, setOpen] = useState(false);

  const handleItem = (handler: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    handler();
  };

  return (
    <div className="relative inline-flex">
      <button
        aria-label="Agents menu"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        className={cn(
          'p-1 rounded-[var(--radius-xs)] transition-colors',
          'hover:bg-[var(--nm-paper-warm)]',
          open && 'bg-[var(--nm-paper-warm)]',
        )}
      >
        <MoreHorizontal className="w-3.5 h-3.5" style={{ color: 'var(--nm-ink50)' }} />
      </button>

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
              icon={<Upload className="w-3 h-3" />}
              label="Import"
              onClick={handleItem(onImport)}
            />
            <MenuItem
              icon={<Package className="w-3 h-3" />}
              label="Export"
              onClick={handleItem(onExport)}
            />
            <MenuItem
              icon={<Users2 className="w-3 h-3" />}
              label="Manage Teams"
              onClick={handleItem(onManageTeams)}
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
