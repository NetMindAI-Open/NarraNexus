/**
 * @file_name: status.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Status & connection primitives — ConnectionBanner (4 states),
 * StatusDot, StatusBadge, Toast. Implements NM Axiom #2 (warm-tinted status
 * palette independent of species).
 *
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type NMStatusKind = 'success' | 'warning' | 'error' | 'info' | 'neutral';

function statusColor(kind: NMStatusKind): string {
  switch (kind) {
    case 'success':
      return 'var(--color-success)';
    case 'warning':
      return 'var(--color-warning)';
    case 'error':
      return 'var(--color-error)';
    case 'info':
      return 'var(--color-info)';
    default:
      return 'var(--nm-ink50)';
  }
}

// ---------------------------------------------------------------------------
// StatusDot — ring (default) or filled small indicator
// ---------------------------------------------------------------------------
export interface StatusDotProps {
  status: NMStatusKind;
  size?: 6 | 8 | 10 | 12;
  filled?: boolean;
  pulse?: boolean;
  className?: string;
  title?: string;
}

export function StatusDot({
  status,
  size = 8,
  filled = true,
  pulse,
  className,
  title,
}: StatusDotProps) {
  const c = statusColor(status);
  return (
    <span
      data-nm="status-dot"
      data-status={status}
      title={title}
      role={title ? 'img' : undefined}
      aria-label={title}
      className={cn(
        'inline-block rounded-full align-middle',
        pulse && 'animate-pulse',
        className
      )}
      style={{
        width: size,
        height: size,
        borderWidth: 1.5,
        borderStyle: 'solid',
        borderColor: c,
        background: filled ? c : 'transparent',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// StatusBadge — hairline pill + dot + label
// ---------------------------------------------------------------------------
export interface StatusBadgeProps {
  status: NMStatusKind;
  children: ReactNode;
  /** If false (default), label text matches status color; if true, ink */
  inkLabel?: boolean;
  className?: string;
}

export function StatusBadge({
  status,
  children,
  inkLabel,
  className,
}: StatusBadgeProps) {
  const c = statusColor(status);
  return (
    <span
      data-nm="status-badge"
      data-status={status}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-[var(--radius-xs)] border px-2 py-0.5 text-xs font-medium uppercase tracking-wider',
        className
      )}
      style={{
        borderColor: c,
        color: inkLabel ? 'var(--nm-ink)' : c,
        background: 'transparent',
      }}
    >
      <StatusDot status={status} size={6} filled />
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ConnectionBanner — non-blocking 4-state top banner
// ---------------------------------------------------------------------------
export type ConnectionState = 'synced' | 'connecting' | 'sync-error' | 'offline';

export interface ConnectionBannerProps {
  state: ConnectionState;
  /** Called when user clicks "Retry" on sync-error */
  onRetry?: () => void;
  className?: string;
}

const STATE_TO_STATUS: Record<Exclude<ConnectionState, 'synced'>, NMStatusKind> = {
  connecting: 'neutral',
  'sync-error': 'error',
  offline: 'neutral',
};

const STATE_LABEL: Record<Exclude<ConnectionState, 'synced'>, string> = {
  connecting: 'Connecting…',
  'sync-error': 'Sync failed',
  offline: 'Currently offline',
};

export function ConnectionBanner({
  state,
  onRetry,
  className,
}: ConnectionBannerProps) {
  if (state === 'synced') return null;
  const status = STATE_TO_STATUS[state];
  return (
    <div
      data-nm="connection-banner"
      data-state={state}
      role="status"
      aria-live="polite"
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 text-xs',
        className
      )}
      style={{
        background: 'var(--nm-paper-warm)',
        color: 'var(--nm-ink70)',
        borderLeft: `4px solid ${statusColor(status)}`,
      }}
    >
      <StatusDot status={status} size={8} filled={state !== 'connecting'} pulse={state === 'connecting'} />
      <span className="flex-1">{STATE_LABEL[state]}</span>
      {state === 'sync-error' && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="font-medium"
          style={{ color: 'var(--color-error)' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast — paper-raised with left status/species color bar
// ---------------------------------------------------------------------------
export interface ToastProps {
  title: ReactNode;
  description?: ReactNode;
  status?: NMStatusKind;
  onDismiss?: () => void;
  /** Optional action button on the right */
  action?: ReactNode;
  className?: string;
}

export function Toast({
  title,
  description,
  status = 'info',
  onDismiss,
  action,
  className,
}: ToastProps) {
  const c = statusColor(status);
  return (
    <div
      data-nm="toast"
      data-status={status}
      role="status"
      aria-live="polite"
      className={cn(
        'flex items-start gap-3 rounded-[var(--radius-md)] p-3 pr-4 min-w-[280px] max-w-[420px]',
        className
      )}
      style={{
        background: 'var(--nm-raised)',
        border: '1px solid var(--nm-hairline)',
        borderLeftWidth: 4,
        borderLeftColor: c,
        boxShadow: 'var(--nm-elev-2)',
        color: 'var(--nm-ink)',
      }}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">{title}</div>
        {description && (
          <div className="mt-0.5 text-xs" style={{ color: 'var(--nm-ink70)' }}>
            {description}
          </div>
        )}
      </div>
      {action}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="opacity-60 hover:opacity-100 transition-opacity"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" strokeWidth="1.5" fill="none">
            <path d="M3 3 L11 11 M11 3 L3 11" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}
