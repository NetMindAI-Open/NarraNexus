/**
 * @file_name: CreateMenu.tsx
 * @author:
 * @date: 2026-06-23
 * @description: The "New" entry in the sidebar's global nav.
 * A full-width nav row that opens a dropdown of everything creatable or
 * importable: Create agent / Create team / Import .nxbundle / Import from
 * other source — one front door for "bring a new thing into NarraNexus".
 *
 * Same inline-panel approach as TeamRowMenu — no Radix portal, so it
 * renders correctly inside the sidebar without portal-positioning issues.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Bot, Users2, Download, Import } from 'lucide-react';
import { useDismissOnOutside } from '@/hooks';
import { cn } from '@/lib/utils';

export interface CreateMenuProps {
  onCreateAgent: () => void;
  onCreateTeam: () => void;
  /** Import a .nxbundle (agent or team bundle). */
  onImportBundle: () => void;
  /** "Import from other source" — migrate from another agent framework.
   *  Only wired in local/desktop mode (the scanner reads the filesystem). */
  onImportAgent?: () => void;
  /** Disables the trigger while an agent is being created. */
  disabled?: boolean;
}

export function CreateMenu({
  onCreateAgent,
  onCreateTeam,
  onImportBundle,
  onImportAgent,
  disabled,
}: CreateMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const handleItem = (handler: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    handler();
  };

  const containerRef = useDismissOnOutside<HTMLDivElement>(open, () => setOpen(false));

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        disabled={disabled}
        title={t('layout.createMenu.newTitle')}
        aria-label={t('layout.createMenu.createAgentOrTeam')}
        className={cn(
          'w-full flex items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)]',
          'text-[13px] font-medium text-left text-[var(--nm-ink)] transition-colors',
          'hover:bg-[var(--nm-paper-warm)]',
          open && 'bg-[var(--nm-raised)]',
          disabled && 'opacity-50 cursor-not-allowed',
        )}
      >
        <Plus className={cn('w-4 h-4 shrink-0', disabled && 'animate-pulse')} />
        <span className="flex-1 min-w-0">{t('layout.createMenu.new')}</span>
      </button>

      {open && (
        <div
          className={cn(
            'absolute left-0 right-0 top-full mt-1 z-50 p-1.5',
            'rounded-[var(--radius-md)] border shadow-[0_8px_24px_rgba(0,0,0,0.14)]',
            'bg-[var(--nm-card)] border-[var(--nm-hairline)]',
          )}
        >
          <MenuItem
            icon={<Bot className="w-3.5 h-3.5" />}
            label={t('layout.createMenu.createAgent')}
            onClick={handleItem(onCreateAgent)}
          />
          <MenuItem
            icon={<Users2 className="w-3.5 h-3.5" />}
            label={t('layout.createMenu.createTeam')}
            onClick={handleItem(onCreateTeam)}
          />
          <MenuItem
            icon={<Download className="w-3.5 h-3.5" />}
            label={t('layout.createMenu.importBundle')}
            onClick={handleItem(onImportBundle)}
          />
          {onImportAgent && (
            <MenuItem
              icon={<Import className="w-3.5 h-3.5" />}
              label={t('layout.createMenu.importAgent')}
              onClick={handleItem(onImportAgent)}
            />
          )}
        </div>
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
        'w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left',
        'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)] transition-colors',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
