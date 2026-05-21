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
    <div className="fixed inset-0 z-[1000]">
      {/* NM warm-ink backdrop with subtle blur (Axiom #4 — paper feel preserved) */}
      <div
        className="fixed inset-0 animate-fade-in"
        style={{ background: 'var(--nm-backdrop)', backdropFilter: 'blur(2px)' }}
        onClick={onClose}
      />

      {/* Dialog body — NM paper-raised + soft lift shadow (Axiom #4 exception) */}
      <div className="fixed inset-0 overflow-y-auto z-[1001]">
        <div className="flex min-h-full items-center justify-center p-4">
          <div
            className={cn(
              'relative w-full',
              'animate-scale-in',
              sizeClasses[size],
              className
            )}
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--nm-raised)',
              border: '1px solid var(--nm-hairline)',
              borderRadius: 'var(--radius-xl)',
              boxShadow: 'var(--nm-elev-3)',
            }}
          >
            <div className="relative">
              {title && (
                <div
                  className="flex items-center justify-between px-5 py-3 border-b"
                  style={{ borderColor: 'var(--nm-hairline)' }}
                >
                  <h2
                    className="text-[12px] font-semibold uppercase tracking-[0.14em]"
                    style={{
                      fontFamily: 'var(--font-display)',
                      color: 'var(--nm-ink)',
                    }}
                  >
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
    <div
      className={cn('flex items-center justify-end gap-2 px-5 py-3 border-t', className)}
      style={{ borderColor: 'var(--nm-hairline)' }}
    >
      {children}
    </div>
  );
}
