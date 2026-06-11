/**
 * @file_name: BookmarkDrawer.tsx
 * @date: 2026-06-10
 * @description: Slide-over shell for bookmark panel content.
 *
 * Two modes:
 *   Slide-over (pinned=false): right-anchored overlay 440px wide.
 *     - bg: var(--nm-paper)
 *     - left-edge shadow: -2px 0 var(--nm-elev-edge)
 *     - transparent backdrop; click backdrop or Esc → onClose
 *     - role="dialog" aria-modal
 *   Pinned (pinned=true): static column frame, no backdrop, no aria-modal.
 *
 * Header: mono uppercase title + Pin/PinOff toggle + X close.
 *
 * The slide-in animation uses CSS via animate-slide-in-right (already
 * defined in the project's Tailwind/CSS config).  Pin state persistence
 * is the caller's responsibility (typically localStorage-backed in the
 * parent).
 */

import { type ReactNode, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Pin, PinOff } from 'lucide-react';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BookmarkDrawerProps {
  open: boolean;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BookmarkDrawer({
  open,
  pinned,
  onPinnedChange,
  onClose,
  title,
  children,
}: BookmarkDrawerProps) {
  // Keyboard Esc handler — only for slide-over mode (not pinned)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !pinned) {
        onClose();
      }
    },
    [onClose, pinned],
  );

  useEffect(() => {
    if (open && !pinned) {
      document.addEventListener('keydown', handleKeyDown);
      return () => {
        document.removeEventListener('keydown', handleKeyDown);
      };
    }
  }, [open, pinned, handleKeyDown]);

  if (!open) return null;

  // Pinned mode: static column frame that FILLS its parent — the parent
  // owns width / border / rounding so the pinned column matches the
  // surrounding card language (no portal, no backdrop).
  if (pinned) {
    return (
      <div
        className="flex flex-col w-full h-full overflow-hidden"
        style={{ background: 'var(--nm-paper)' }}
      >
        <DrawerHeader
          title={title}
          pinned={pinned}
          onPinnedChange={onPinnedChange}
          onClose={onClose}
        />
        <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
      </div>
    );
  }

  // Slide-over mode: portal overlay
  return createPortal(
    <div className="fixed inset-y-0 right-0 z-[200] flex pointer-events-none">
      {/* Transparent backdrop — covers the page to capture outside clicks */}
      <div
        className="fixed inset-0 pointer-events-auto"
        data-drawer-backdrop=""
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div
        role="dialog"
        aria-modal={true}
        aria-label={title}
        className={cn(
          'relative flex flex-col w-[440px] h-full pointer-events-auto',
          'animate-slide-in-right',
        )}
        style={{
          background: 'var(--nm-paper)',
          boxShadow: '-2px 0 var(--nm-elev-edge)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <DrawerHeader
          title={title}
          pinned={pinned}
          onPinnedChange={onPinnedChange}
          onClose={onClose}
        />
        <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Header sub-component
// ---------------------------------------------------------------------------

interface DrawerHeaderProps {
  title: string;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  onClose: () => void;
}

function DrawerHeader({ title, pinned, onPinnedChange, onClose }: DrawerHeaderProps) {
  return (
    <div
      className="flex items-center justify-between gap-2 px-4 py-3 shrink-0"
      style={{ borderBottom: '1px solid var(--nm-hairline)' }}
    >
      {/* Mono uppercase title */}
      <span
        className="text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] leading-none"
        style={{ color: 'var(--text-primary)' }}
      >
        {title}
      </span>

      <div className="flex items-center gap-1">
        {/* Pin / PinOff toggle */}
        {pinned ? (
          <button
            type="button"
            aria-label="Unpin panel"
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded-sm',
              'transition-colors duration-100 cursor-pointer',
              'hover:bg-[var(--nm-paper-warm)]',
            )}
            onClick={() => onPinnedChange(false)}
          >
            <PinOff
              className="w-3.5 h-3.5"
              style={{ color: 'var(--text-secondary)' }}
              aria-hidden
            />
          </button>
        ) : (
          <button
            type="button"
            aria-label="Pin panel"
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded-sm',
              'transition-colors duration-100 cursor-pointer',
              'hover:bg-[var(--nm-paper-warm)]',
            )}
            onClick={() => onPinnedChange(true)}
          >
            <Pin
              className="w-3.5 h-3.5"
              style={{ color: 'var(--text-secondary)' }}
              aria-hidden
            />
          </button>
        )}

        {/* Close button */}
        <button
          type="button"
          aria-label="Close panel"
          className={cn(
            'flex items-center justify-center w-6 h-6 rounded-sm',
            'transition-colors duration-100 cursor-pointer',
            'hover:bg-[var(--nm-paper-warm)]',
          )}
          onClick={onClose}
        >
          <X
            className="w-3.5 h-3.5"
            style={{ color: 'var(--text-secondary)' }}
            aria-hidden
          />
        </button>
      </div>
    </div>
  );
}
