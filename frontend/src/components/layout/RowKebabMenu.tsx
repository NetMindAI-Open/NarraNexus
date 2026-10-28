/**
 * @file_name: RowKebabMenu.tsx
 * @author:
 * @date: 2026-09-11
 * @description: Shared kebab (⋮) dropdown shell for sidebar rows — the
 * trigger button, the inline absolute panel and its items. AgentRowMenu and
 * TeamRowMenu are thin item lists on top of it, so the panel's look and its
 * dismiss behaviour live in one place.
 *
 * Inline absolute panel (no portal) so it works inside the sidebar scroll
 * container. Outside click / Escape dismissal is document-level
 * (useDismissOnOutside): a full-screen backdrop would be trapped inside the
 * row's transform stacking context and cover only that row.
 */

import { useState, type ReactNode } from 'react';
import { MoreVertical } from 'lucide-react';
import { useDismissOnOutside } from '@/hooks';
import { cn } from '@/lib/utils';

export interface RowKebabMenuItem {
  key: string;
  icon: ReactNode;
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onSelect: (e: React.MouseEvent) => void;
}

export interface RowKebabMenuProps {
  /** Accessible name of the trigger button. */
  ariaLabel: string;
  items: RowKebabMenuItem[];
  /** Tailwind min-width class of the panel. */
  minWidthClass?: string;
  /** Fired on open/close so the host row can lift its z-index above the rows
   *  below (each row is its own stacking context — animate-slide-up retains a
   *  transform — so the panel's own z-index cannot escape it). */
  onOpenChange?: (open: boolean) => void;
}

export function RowKebabMenu({ ariaLabel, items, minWidthClass = 'min-w-[120px]', onOpenChange }: RowKebabMenuProps) {
  const [open, setOpen] = useState(false);
  // Notify the parent from the event handler (NOT inside a setState updater —
  // that runs during render and triggers a cross-component setState warning).
  const setOpenAndNotify = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  const containerRef = useDismissOnOutside<HTMLDivElement>(open, () => setOpenAndNotify(false));

  // Every click stops here: the row's own onClick selects the row, and a menu
  // action must not do that as a side effect.
  const handleItem = (item: RowKebabMenuItem) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenAndNotify(false);
    item.onSelect(e);
  };

  return (
    <div ref={containerRef} className="relative inline-flex" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
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
        <div
          className={cn(
            'absolute right-0 top-full mt-0.5 z-50',
            minWidthClass,
            'py-0.5',
            'rounded-[var(--radius-sm)] border shadow-md',
            'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
          )}
        >
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={handleItem(item)}
              disabled={item.disabled}
              className={cn(
                'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors',
                item.disabled && 'opacity-50 cursor-not-allowed',
                item.danger
                  ? 'text-[var(--color-error)] hover:bg-[var(--color-error)]/10'
                  : 'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)]',
              )}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
