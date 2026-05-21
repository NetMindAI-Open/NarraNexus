/**
 * @file_name: modal.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Modal primitives — Dialog, Drawer, ConfirmDialog, Sheet.
 * Implements Axiom #4 (warm-ink backdrop, paper-raised card, NM bracket
 * corner marks for focus emphasis).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.10
 */

import { useEffect, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { BracketCornerMarks } from './bracket';
import { Button } from './button';

// ---------------------------------------------------------------------------
// Internal: Backdrop overlay
// ---------------------------------------------------------------------------
function Backdrop({ onClick }: { onClick?: () => void }) {
  return (
    <div
      data-nm="modal-backdrop"
      aria-hidden="true"
      onClick={onClick}
      className="fixed inset-0 z-[1000] animate-fade-in"
      style={{
        background: 'var(--nm-backdrop)',
        backdropFilter: 'blur(2px)',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Internal: lock body scroll when modal is open
// ---------------------------------------------------------------------------
function useScrollLock(open: boolean) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);
}

// ---------------------------------------------------------------------------
// Internal: Escape-to-close
// ---------------------------------------------------------------------------
function useEscapeClose(open: boolean, onClose?: () => void) {
  useEffect(() => {
    if (!open || !onClose) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);
}

// ---------------------------------------------------------------------------
// Dialog — center modal with bracket corner marks
// ---------------------------------------------------------------------------
export interface DialogProps {
  open: boolean;
  onClose?: () => void;
  title?: ReactNode;
  /** If true, the close X button is shown in the corner */
  showClose?: boolean;
  /** Max width preset */
  size?: 'sm' | 'md' | 'lg';
  children: ReactNode;
  /** Optional footer slot (typically Button group) */
  footer?: ReactNode;
  /** Show four-corner bracket marks (default true for "focused container") */
  cornerMarks?: boolean;
  className?: string;
}

const DIALOG_WIDTH: Record<NonNullable<DialogProps['size']>, string> = {
  sm: 'max-w-md',
  md: 'max-w-lg',
  lg: 'max-w-3xl',
};

export function Dialog({
  open,
  onClose,
  title,
  showClose = true,
  size = 'md',
  children,
  footer,
  cornerMarks = true,
  className,
}: DialogProps) {
  useScrollLock(open);
  useEscapeClose(open, onClose);
  if (!open) return null;
  const inner = (
    <div
      data-nm="dialog"
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === 'string' ? title : undefined}
      className={cn(
        'w-full p-6 rounded-[var(--radius-xl)]',
        'animate-scale-in',
        className
      )}
      style={{
        background: 'var(--nm-raised)',
        border: '1px solid var(--nm-hairline)',
        boxShadow: 'var(--nm-elev-3)',
      }}
    >
      {(title || showClose) && (
        <div className="flex items-start justify-between mb-4 gap-4">
          {title && (
            <h2
              className="text-lg font-semibold"
              style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
            >
              {title}
            </h2>
          )}
          {showClose && onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="opacity-60 hover:opacity-100 transition-opacity p-1 -m-1"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3.5 3.5L12.5 12.5M12.5 3.5L3.5 12.5" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>
      )}
      <div className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
        {children}
      </div>
      {footer && (
        <div className="mt-6 flex items-center justify-end gap-2">{footer}</div>
      )}
    </div>
  );
  return (
    <>
      <Backdrop onClick={onClose} />
      <div
        className={cn(
          'fixed inset-0 z-[1001] flex items-center justify-center p-4 pointer-events-none',
        )}
      >
        <div className={cn('pointer-events-auto w-full', DIALOG_WIDTH[size])}>
          {cornerMarks ? (
            <BracketCornerMarks cornerSize={14}>{inner}</BracketCornerMarks>
          ) : (
            inner
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// ConfirmDialog — yes/no with destructive variant
// ---------------------------------------------------------------------------
export interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive,
  loading,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onCancel}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {message}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Drawer — slide-in side panel
// ---------------------------------------------------------------------------
export interface DrawerProps {
  open: boolean;
  onClose?: () => void;
  side?: 'left' | 'right';
  /** Width preset */
  size?: 'sm' | 'md' | 'lg';
  title?: ReactNode;
  showClose?: boolean;
  children: ReactNode;
  className?: string;
}

const DRAWER_WIDTH: Record<NonNullable<DrawerProps['size']>, string> = {
  sm: 'w-[320px]',
  md: 'w-[420px]',
  lg: 'w-[560px]',
};

export function Drawer({
  open,
  onClose,
  side = 'right',
  size = 'md',
  title,
  showClose = true,
  children,
  className,
}: DrawerProps) {
  useScrollLock(open);
  useEscapeClose(open, onClose);
  if (!open) return null;
  const isRight = side === 'right';
  const slideClass = isRight ? 'animate-slide-in-right' : 'animate-slide-in-left';
  return (
    <>
      <Backdrop onClick={onClose} />
      <div
        data-nm="drawer"
        data-side={side}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        className={cn(
          'fixed top-0 bottom-0 z-[1001] flex flex-col',
          slideClass,
          isRight ? 'right-0' : 'left-0',
          DRAWER_WIDTH[size],
          className
        )}
        style={{
          background: 'var(--nm-card)',
          borderLeft: isRight ? '1px solid var(--nm-hairline)' : undefined,
          borderRight: !isRight ? '1px solid var(--nm-hairline)' : undefined,
          boxShadow: isRight
            ? `-2px 0 var(--nm-elev-edge)`
            : `2px 0 var(--nm-elev-edge)`,
        }}
      >
        {(title || showClose) && (
          <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: 'var(--nm-hairline)' }}>
            {title && (
              <h2
                className="text-base font-semibold"
                style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
              >
                {title}
              </h2>
            )}
            {showClose && onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close drawer"
                className="opacity-60 hover:opacity-100 transition-opacity p-1 -m-1"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M3.5 3.5L12.5 12.5M12.5 3.5L3.5 12.5" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sheet — mobile bottom-sheet
// ---------------------------------------------------------------------------
export interface SheetProps {
  open: boolean;
  onClose?: () => void;
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Sheet({
  open,
  onClose,
  title,
  children,
  className,
}: SheetProps) {
  useScrollLock(open);
  useEscapeClose(open, onClose);
  if (!open) return null;
  return (
    <>
      <Backdrop onClick={onClose} />
      <div
        data-nm="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        className={cn(
          'fixed bottom-0 left-0 right-0 z-[1001] animate-slide-up',
          className
        )}
        style={{
          background: 'var(--nm-card)',
          borderTopLeftRadius: 'var(--radius-xl)',
          borderTopRightRadius: 'var(--radius-xl)',
          maxHeight: '85vh',
          boxShadow: 'var(--nm-elev-sheet)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Drag handle */}
        <div
          aria-hidden="true"
          className="self-center mt-2 mb-1"
          style={{ width: 36, height: 4, borderRadius: 9999, background: 'var(--nm-ink30)' }}
        />
        {title && (
          <h2
            className="px-4 pt-2 pb-3 text-base font-semibold"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            {title}
          </h2>
        )}
        <div className="px-4 pb-6 overflow-y-auto">{children}</div>
      </div>
    </>
  );
}
