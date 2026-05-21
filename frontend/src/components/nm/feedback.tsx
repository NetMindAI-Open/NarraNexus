/**
 * @file_name: feedback.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Feedback primitives — Skeleton, Spinner, ProgressBar.
 * Implements NM Axiom #4 (lift via paper not shadow) and #8 (motion =
 * paper rhythm).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.9
 */

import { cn } from '@/lib/utils';
import type { NMSpecies } from './identity';

// ---------------------------------------------------------------------------
// Skeleton — shimmer placeholder shape
// ---------------------------------------------------------------------------
export interface SkeletonProps {
  /** Shape variant */
  variant?: 'rect' | 'text' | 'circle';
  /** Width in px or any CSS value (string) */
  width?: number | string;
  /** Height in px or any CSS value (string) */
  height?: number | string;
  /** Number of text lines (text variant only) */
  lines?: number;
  className?: string;
}

export function Skeleton({
  variant = 'rect',
  width,
  height,
  lines = 1,
  className,
}: SkeletonProps) {
  if (variant === 'text') {
    return (
      <div data-nm="skeleton" data-variant="text" className={cn('flex flex-col gap-2', className)}>
        {Array.from({ length: lines }).map((_, i) => (
          <span
            key={i}
            className="skeleton block"
            style={{
              height: height ?? 12,
              width: typeof width === 'number' ? width : (width ?? (i === lines - 1 ? '60%' : '100%')),
              borderRadius: 'var(--radius-xs)',
            }}
          />
        ))}
      </div>
    );
  }
  const isCircle = variant === 'circle';
  return (
    <span
      data-nm="skeleton"
      data-variant={variant}
      className={cn('skeleton block', className)}
      style={{
        width: width ?? (isCircle ? 40 : '100%'),
        height: height ?? (isCircle ? 40 : 16),
        borderRadius: isCircle ? '9999px' : 'var(--radius-sm)',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Spinner — single thin rotating ring
// ---------------------------------------------------------------------------
export interface SpinnerProps {
  size?: number;
  /** Color: 'ink' (default) or species color */
  species?: NMSpecies;
  className?: string;
  /** Accessible label for screen readers */
  label?: string;
}

function speciesColor(species: NMSpecies): string {
  if (species === 'carbon') return 'var(--color-carbon)';
  if (species === 'silicon') return 'var(--color-silicon)';
  if (species === 'overlap') return 'var(--color-overlap)';
  return 'var(--nm-ink)';
}

export function Spinner({
  size = 18,
  species = 'neutral',
  className,
  label = 'Loading',
}: SpinnerProps) {
  return (
    <span
      data-nm="spinner"
      role="status"
      aria-label={label}
      className={cn('inline-block animate-spin rounded-full', className)}
      style={{
        width: size,
        height: size,
        borderWidth: 1.5,
        borderStyle: 'solid',
        borderColor: speciesColor(species),
        borderRightColor: 'transparent',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// ProgressBar — thin ink rail + ink fill (or species fill)
// ---------------------------------------------------------------------------
export interface ProgressBarProps {
  /** 0-100 percentage */
  value: number;
  /** Max value (default 100) */
  max?: number;
  /** Color: 'ink' (default) or species */
  species?: NMSpecies;
  /** Optional label shown above bar */
  label?: string;
  /** Show percentage at right of label */
  showPercent?: boolean;
  /** Bar height in px */
  height?: number;
  className?: string;
}

export function ProgressBar({
  value,
  max = 100,
  species = 'neutral',
  label,
  showPercent = false,
  height = 4,
  className,
}: ProgressBarProps) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const fillColor = species === 'neutral' ? 'var(--nm-ink)' : speciesColor(species);
  return (
    <div
      data-nm="progress-bar"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemax={max}
      aria-valuemin={0}
      aria-label={label ?? `${Math.round(pct)}%`}
      className={cn('w-full', className)}
    >
      {label && (
        <div className="flex items-baseline justify-between text-xs mb-1.5" style={{ color: 'var(--nm-ink70)' }}>
          <span>{label}</span>
          {showPercent && (
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}>
              {Math.round(pct)}%
            </span>
          )}
        </div>
      )}
      <div
        className="w-full overflow-hidden"
        style={{
          height,
          background: 'rgba(42,38,32,0.10)',
          borderRadius: height >= 6 ? 'var(--radius-xs)' : 0,
        }}
      >
        <div
          data-nm="progress-fill"
          className="h-full transition-[width] duration-300"
          style={{
            width: `${pct}%`,
            background: fillColor,
          }}
        />
      </div>
    </div>
  );
}
