/**
 * @file_name: BookmarkDrawer.tsx
 * @date: 2026-06-10
 * @description: Slide-over shell for bookmark panel content.
 *
 * Two modes:
 *   Slide-over (pinned=false): overlay 440px wide, anchored `edgeReservePx`
 *     from the right edge so it stops SHORT of the bookmark strip.
 *     - bg: var(--nm-paper)
 *     - left-edge shadow: -2px 0 var(--nm-elev-edge)
 *     - transparent backdrop (also stops short of the strip); click backdrop
 *       or Esc → onClose
 *     - role="dialog", NOT aria-modal — the strip stays operable
 *   Pinned (pinned=true): static column, laid out in the flex row by the
 *     parent. Owns its own frame + width; no portal, no backdrop.
 *
 * Header: mono uppercase title + Pin/PinOff toggle + X close.
 *
 * Toggling pin must not remount the panel (2026-07-30)
 * ----------------------------------------------------
 * React has no reparent primitive: moving a subtree to a different DOM parent
 * always unmounts and remounts it, discarding its state. So the ONLY way the
 * pin toggle can preserve what the user set up inside the panel (job filter,
 * view mode, expanded rows, scroll) is for the panel's DOM position to never
 * move at all. Two consequences, both load-bearing:
 *
 *   1. **No portal.** The slide-over is `position: fixed` (out of flow, so it
 *      consumes no layout space) rendered right where the pinned column would
 *      be. Wrapping in `createPortal` for one mode and not the other is itself
 *      a tree-shape change and remounts just the same — that was the first
 *      attempted fix, and the test suite caught it.
 *   2. **One element, stable slot.** Both modes are the SAME `<div>` with
 *      different classes/styles, in a fragment whose child positions don't
 *      shift. Don't "clean this up" into `if (pinned) return …` — two returns
 *      with different shapes remount the children.
 *
 * The no-portal choice does move the overlay into `<main>`'s stacking context
 * (`relative z-10`), so it no longer paints over the fixed sidebar (z-40).
 * They only overlap on mobile with the off-canvas nav open, where the nav
 * covering content is the expected behaviour anyway.
 *
 * The slide-in animation uses CSS via animate-slide-in-right (already
 * defined in the project's Tailwind/CSS config).  Pin state persistence
 * is the caller's responsibility (typically localStorage-backed in the
 * parent).
 */

import { type ReactNode, type Ref, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
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
  /**
   * Width of the right edge the slide-over must NOT cover — the bookmark
   * strip plus the layout gutter (see MainLayout). Both the panel and the
   * click-capturing backdrop are inset by this much, which is what makes
   * "click another tab" switch panels in ONE click instead of requiring the
   * user to close this one first. Ignored in pinned mode. Default 0.
   */
  edgeReservePx?: number;
  /** Column width in px, pinned mode only. Ignored by the slide-over. */
  pinnedWidth?: number;
  /**
   * Handle on the pinned column, so the parent's ResizableDivider can write
   * `width` straight to the DOM during a drag. Null in slide-over mode.
   */
  columnRef?: Ref<HTMLDivElement>;
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
  edgeReservePx = 0,
  pinnedWidth = 400,
  columnRef,
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

  const overlay = !pinned;

  return (
    <>
      {/* Transparent backdrop — slide-over only. It captures outside clicks
          but leaves the reserved edge alone, so strip clicks reach the strip.
          When pinned this renders as `false`, which is fine: the panel below
          keeps a stable slot in this fragment either way. */}
      {overlay && (
        <div
          className="fixed inset-y-0 left-0 z-[200]"
          style={{ right: edgeReservePx }}
          data-drawer-backdrop=""
          onClick={onClose}
        />
      )}

      {/* The panel. ONE element for both modes — only its positioning changes.
          Slide-over is `position: fixed` (out of flow, so it consumes no layout
          space) rather than a portal; pinned is an in-flow flex column.
          NOT aria-modal in overlay mode: the bookmark strip beside it is a live
          switcher and aria-modal would hide it from screen readers. */}
      <div
        ref={columnRef}
        role={overlay ? 'dialog' : undefined}
        aria-label={overlay ? title : undefined}
        className={cn(
          'flex flex-col overflow-hidden',
          overlay
            ? 'fixed inset-y-0 z-[200] animate-slide-in-right'
            : 'shrink-0 rounded-[var(--radius-md)]',
        )}
        style={
          overlay
            ? {
                right: edgeReservePx,
                width: `min(440px, 100vw - ${edgeReservePx}px)`,
                background: 'var(--nm-paper)',
                boxShadow: '-2px 0 var(--nm-elev-edge)',
              }
            : {
                width: pinnedWidth,
                background: 'var(--nm-paper)',
                border: '1px solid var(--nm-hairline)',
              }
        }
        onClick={overlay ? (e) => e.stopPropagation() : undefined}
      >
        <DrawerHeader
          title={title}
          pinned={pinned}
          onPinnedChange={onPinnedChange}
          onClose={onClose}
        />
        <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
      </div>
    </>
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
  const { t } = useTranslation();
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
            aria-label={t('bookmarks.drawer.unpin')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.unpin')}
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
            aria-label={t('bookmarks.drawer.pin')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.pin')}
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
          aria-label={t('bookmarks.drawer.close')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.close')}
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
