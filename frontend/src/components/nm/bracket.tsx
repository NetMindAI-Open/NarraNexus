/**
 * @file_name: bracket.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Bracket vocabulary — the NM "syntax mark" motif extended to
 * logo, edges, section labels, corner marks, empty state, dropzone, loading.
 * Implements Axiom #6 (bracket as a universal syntax mark for "container /
 * quote / piece of context").
 *
 */

import type { CSSProperties, ReactNode, DragEventHandler } from 'react';
import { cn } from '@/lib/utils';

export type BracketCorner = 'tl' | 'tr' | 'bl' | 'br';
export type BracketSpecies = 'carbon' | 'silicon' | 'overlap' | 'ink' | 'success' | 'warning' | 'error';

function bracketColor(species: BracketSpecies): string {
  switch (species) {
    case 'carbon':
      return 'var(--color-carbon)';
    case 'silicon':
      return 'var(--color-silicon)';
    case 'overlap':
      return 'var(--color-overlap)';
    case 'success':
      return 'var(--color-success)';
    case 'warning':
      return 'var(--color-warning)';
    case 'error':
      return 'var(--color-error)';
    default:
      return 'var(--nm-ink50)';
  }
}

// ---------------------------------------------------------------------------
// BracketMarkLogo — `[ • • ] narra` brand mark
// ---------------------------------------------------------------------------
export interface BracketMarkLogoProps {
  /** If true, include the italic "narra" wordmark next to the mark */
  showWordmark?: boolean;
  /** Total height in px (mark scales proportionally) */
  size?: number;
  className?: string;
}

export function BracketMarkLogo({
  showWordmark = true,
  size = 40,
  className,
}: BracketMarkLogoProps) {
  const markWidth = Math.round(size * 1.55);
  const dotSize = Math.round(size * 0.2);
  const stroke = Math.max(1.5, Math.round(size * 0.05));
  const cornerLen = Math.round(size * 0.28);
  return (
    <div
      data-nm="bracket-mark-logo"
      className={cn('inline-flex items-center gap-3 select-none', className)}
    >
      <div
        style={{
          width: markWidth,
          height: size,
          position: 'relative',
          color: 'var(--nm-ink)',
        }}
      >
        {/* Left bracket */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: cornerLen,
            borderLeft: `${stroke}px solid currentColor`,
            borderTop: `${stroke}px solid currentColor`,
            borderBottom: `${stroke}px solid currentColor`,
          }}
        />
        {/* Right bracket */}
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 0,
            bottom: 0,
            width: cornerLen,
            borderRight: `${stroke}px solid currentColor`,
            borderTop: `${stroke}px solid currentColor`,
            borderBottom: `${stroke}px solid currentColor`,
          }}
        />
        {/* Dots */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: Math.round(size * 0.1),
          }}
        >
          <span style={{ width: dotSize, height: dotSize, borderRadius: '50%', background: 'var(--color-carbon)' }} />
          <span style={{ width: dotSize, height: dotSize, borderRadius: '50%', background: 'var(--color-silicon)' }} />
        </div>
      </div>
      {showWordmark && (
        <span
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: Math.round(size * 0.65),
            fontStyle: 'italic',
            fontWeight: 500,
            color: 'var(--nm-ink)',
            letterSpacing: '-0.01em',
          }}
        >
          narra
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BracketEdge — small bracket-corner angle for message bubbles, etc.
// ---------------------------------------------------------------------------
export interface BracketEdgeProps {
  corner: BracketCorner;
  species?: BracketSpecies;
  /** Edge length in px (8-12 typical) */
  size?: number;
  stroke?: number;
  className?: string;
}

export function BracketEdge({
  corner,
  species = 'ink',
  size = 10,
  stroke = 1.5,
  className,
}: BracketEdgeProps) {
  const color = bracketColor(species);
  const baseStyle: CSSProperties = {
    position: 'absolute',
    width: size,
    height: size,
    borderColor: color,
    borderStyle: 'solid',
    pointerEvents: 'none',
  };
  let cornerStyle: CSSProperties;
  switch (corner) {
    case 'tl':
      cornerStyle = {
        ...baseStyle,
        top: -1,
        left: -1,
        borderWidth: `${stroke}px 0 0 ${stroke}px`,
      };
      break;
    case 'tr':
      cornerStyle = {
        ...baseStyle,
        top: -1,
        right: -1,
        borderWidth: `${stroke}px ${stroke}px 0 0`,
      };
      break;
    case 'bl':
      cornerStyle = {
        ...baseStyle,
        bottom: -1,
        left: -1,
        borderWidth: `0 0 ${stroke}px ${stroke}px`,
      };
      break;
    case 'br':
      cornerStyle = {
        ...baseStyle,
        bottom: -1,
        right: -1,
        borderWidth: `0 ${stroke}px ${stroke}px 0`,
      };
      break;
  }
  return (
    <span
      data-nm="bracket-edge"
      data-corner={corner}
      data-species={species}
      className={cn(className)}
      style={cornerStyle}
      aria-hidden="true"
    />
  );
}

// ---------------------------------------------------------------------------
// BracketSectionLabel — `[ ACTIVE AGENTS ]` uppercase mono section header
// ---------------------------------------------------------------------------
export interface BracketSectionLabelProps {
  children: ReactNode;
  className?: string;
  /** Optional right-side content (count badge, action button) */
  trailing?: ReactNode;
}

export function BracketSectionLabel({
  children,
  className,
  trailing,
}: BracketSectionLabelProps) {
  return (
    <div
      data-nm="bracket-section-label"
      className={cn(
        'flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em]',
        className
      )}
      style={{
        fontFamily: 'var(--font-mono)',
        color: 'var(--nm-ink50)',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 8,
          height: 10,
          borderLeft: '1.5px solid var(--nm-ink30)',
          borderTop: '1.5px solid var(--nm-ink30)',
          borderBottom: '1.5px solid var(--nm-ink30)',
        }}
      />
      <span className="flex-1">{children}</span>
      {trailing}
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 8,
          height: 10,
          borderRight: '1.5px solid var(--nm-ink30)',
          borderTop: '1.5px solid var(--nm-ink30)',
          borderBottom: '1.5px solid var(--nm-ink30)',
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BracketCornerMarks — 4 corner brackets, used as selection marker
// ---------------------------------------------------------------------------
export interface BracketCornerMarksProps {
  children: ReactNode;
  /** Color of the corners */
  species?: BracketSpecies;
  /** Size of each corner in px */
  cornerSize?: number;
  stroke?: number;
  className?: string;
}

export function BracketCornerMarks({
  children,
  species = 'ink',
  cornerSize = 12,
  stroke = 1.5,
  className,
}: BracketCornerMarksProps) {
  return (
    <div
      data-nm="bracket-corner-marks"
      className={cn('relative', className)}
    >
      {children}
      <BracketEdge corner="tl" species={species} size={cornerSize} stroke={stroke} />
      <BracketEdge corner="tr" species={species} size={cornerSize} stroke={stroke} />
      <BracketEdge corner="bl" species={species} size={cornerSize} stroke={stroke} />
      <BracketEdge corner="br" species={species} size={cornerSize} stroke={stroke} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BracketEmptyState — empty state with bracket-wrapped large text
// ---------------------------------------------------------------------------
export interface BracketEmptyStateProps {
  /** Main label e.g. "暂无对话" or "No agents yet" */
  label: ReactNode;
  /** Optional sub-label */
  hint?: ReactNode;
  /** Optional CTA element (typically a Button) */
  cta?: ReactNode;
  className?: string;
}

export function BracketEmptyState({
  label,
  hint,
  cta,
  className,
}: BracketEmptyStateProps) {
  return (
    <div
      data-nm="bracket-empty-state"
      className={cn(
        'flex flex-col items-center justify-center text-center py-12 px-6 gap-4',
        className
      )}
    >
      <div
        className="flex items-center gap-3 text-[color:var(--nm-ink30)]"
        style={{
          fontFamily: 'var(--font-display)',
          fontSize: 22,
          fontWeight: 500,
          letterSpacing: '-0.01em',
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 14,
            height: 24,
            borderLeft: '1.5px solid currentColor',
            borderTop: '1.5px solid currentColor',
            borderBottom: '1.5px solid currentColor',
          }}
        />
        <span>{label}</span>
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 14,
            height: 24,
            borderRight: '1.5px solid currentColor',
            borderTop: '1.5px solid currentColor',
            borderBottom: '1.5px solid currentColor',
          }}
        />
      </div>
      {hint && (
        <p
          className="text-sm"
          style={{ color: 'var(--nm-ink50)', maxWidth: 360 }}
        >
          {hint}
        </p>
      )}
      {cta}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BracketDropzone — file upload zone with diagonal bracket corners
// ---------------------------------------------------------------------------
export interface BracketDropzoneProps {
  children?: ReactNode;
  /** Visual active state when a drag is over */
  active?: boolean;
  onDrop?: DragEventHandler<HTMLDivElement>;
  onDragOver?: DragEventHandler<HTMLDivElement>;
  onDragEnter?: DragEventHandler<HTMLDivElement>;
  onDragLeave?: DragEventHandler<HTMLDivElement>;
  className?: string;
}

export function BracketDropzone({
  children,
  active,
  onDrop,
  onDragOver,
  onDragEnter,
  onDragLeave,
  className,
}: BracketDropzoneProps) {
  return (
    <div
      data-nm="bracket-dropzone"
      data-active={active ? 'true' : 'false'}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      className={cn(
        'relative p-12 flex flex-col items-center justify-center text-center transition-colors',
        className
      )}
      style={{
        minHeight: 220,
        color: active ? 'var(--color-carbon)' : 'var(--nm-ink50)',
        background: active ? 'var(--color-carbon-soft)' : 'transparent',
      }}
    >
      {/* tl diagonal bracket */}
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: 8,
          left: 8,
          width: 40,
          height: 40,
          borderLeft: '1.5px dashed currentColor',
          borderTop: '1.5px dashed currentColor',
        }}
      />
      {/* br diagonal bracket */}
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          bottom: 8,
          right: 8,
          width: 40,
          height: 40,
          borderRight: '1.5px dashed currentColor',
          borderBottom: '1.5px dashed currentColor',
        }}
      />
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BracketLoading — `[ 加载中 · · · ]` placeholder
// ---------------------------------------------------------------------------
export interface BracketLoadingProps {
  /** Loading label, e.g. "加载中" or "Loading" */
  label?: string;
  className?: string;
}

export function BracketLoading({
  label = 'Loading',
  className,
}: BracketLoadingProps) {
  return (
    <div
      data-nm="bracket-loading"
      role="status"
      aria-live="polite"
      className={cn(
        'inline-flex items-center gap-3 text-sm',
        className
      )}
      style={{
        fontFamily: 'var(--font-mono)',
        color: 'var(--nm-ink50)',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 8,
          height: 14,
          borderLeft: '1.5px solid currentColor',
          borderTop: '1.5px solid currentColor',
          borderBottom: '1.5px solid currentColor',
        }}
      />
      <span>{label}</span>
      <span className="inline-flex gap-1" aria-hidden="true">
        <span className="animate-typing-cursor" style={{ animationDelay: '0ms' }}>·</span>
        <span className="animate-typing-cursor" style={{ animationDelay: '200ms' }}>·</span>
        <span className="animate-typing-cursor" style={{ animationDelay: '400ms' }}>·</span>
      </span>
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 8,
          height: 14,
          borderRight: '1.5px solid currentColor',
          borderTop: '1.5px solid currentColor',
          borderBottom: '1.5px solid currentColor',
        }}
      />
    </div>
  );
}
