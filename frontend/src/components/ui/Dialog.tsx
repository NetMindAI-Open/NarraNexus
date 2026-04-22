/**
 * Dialog — Nordic archive style
 * Flat modal, 1px ink border, DM Mono header, no glow.
 */

import { useEffect, useCallback, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from './Button';

interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl' | '6xl';
}

export function Dialog({ isOpen, onClose, title, children, className, size = 'md' }: DialogProps) {
  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    }
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleEscape]);

  if (!isOpen) return null;

  const sizeClasses = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    '2xl': 'max-w-2xl',
    '3xl': 'max-w-3xl',
    '4xl': 'max-w-4xl',
    '5xl': 'max-w-5xl',
    '6xl': 'max-w-6xl',
  };

  return createPortal(
    <div className="fixed inset-0 z-50">
      {/* Backdrop — flat ink, no blur (cheap to paint, archival) */}
      <div
        className="fixed inset-0 bg-[rgba(17,18,20,0.6)] animate-fade-in"
        onClick={onClose}
      />

      {/* Dialog */}
      <div className="fixed inset-0 overflow-y-auto">
        <div className="flex min-h-full items-center justify-center p-4">
          <div
            className={cn(
              'relative w-full',
              'bg-[var(--bg-primary)]',
              'border border-[var(--text-primary)]',
              'animate-slide-up',
              sizeClasses[size],
              className
            )}
            onClick={(e) => e.stopPropagation()}
            style={{ borderRadius: 0 }}
          >
            <div className="relative">
              {title && (
                <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--rule)]">
                  <h2 className="text-[11px] font-medium uppercase font-[family-name:var(--font-mono)] tracking-[0.18em] text-[var(--text-primary)]">
                    {title}
                  </h2>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    className="w-7 h-7"
                  >
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              )}

              <div className={cn(!title && 'pt-2')}>
                {children}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

export function DialogContent({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('p-5', className)}>
      {children}
    </div>
  );
}

export function DialogFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--rule)]', className)}>
      {children}
    </div>
  );
}
