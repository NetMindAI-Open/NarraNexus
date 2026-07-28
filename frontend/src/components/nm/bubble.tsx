/**
 * @file_name: bubble.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Bubble primitives — MessageBubble (7 variants), BubbleGroup,
 * BubbleMetaRow. Implements NM Axiom #4 (paper-raised own bubble), #5
 * (radius-lg = 14px), #6 (bracket-edge per species).
 *
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { BracketEdge } from './bracket';

export type NMBubbleVariant =
  | 'human-other'   // someone else's human message
  | 'ai-other'      // another agent's message
  | 'own'           // self, default paper-raised
  | 'own-lilac'     // self + AI co-write (overlap moment)
  | 'system'        // "Jane joined" — centered ink-50, no bubble
  | 'tool-result'   // inline tool output (sunken)
  | 'error';        // LLM/tool failure (warm-oxblood tint)

// ---------------------------------------------------------------------------
// MessageBubble
// ---------------------------------------------------------------------------
export interface MessageBubbleProps {
  variant: NMBubbleVariant;
  children: ReactNode;
  /** Optional accessible label, e.g. "Jane Chen at 12:04" */
  ariaLabel?: string;
  className?: string;
}

interface VariantStyle {
  align: 'left' | 'right' | 'center';
  bg: string;
  color: string;
  border?: string;
  edge?: { corner: 'tl' | 'tr'; species: 'carbon' | 'silicon' | 'ink' | 'overlap' | 'error' };
  showBubble: boolean;
  extraClass?: string;
}

const VARIANT: Record<NMBubbleVariant, VariantStyle> = {
  'human-other': {
    align: 'left',
    bg: 'var(--nm-paper-warm)',
    color: 'var(--nm-ink)',
    edge: { corner: 'tl', species: 'carbon' },
    showBubble: true,
  },
  'ai-other': {
    align: 'left',
    bg: 'var(--nm-paper-warm)',
    color: 'var(--nm-ink)',
    edge: { corner: 'tl', species: 'silicon' },
    showBubble: true,
  },
  own: {
    align: 'right',
    bg: 'var(--nm-own-paper)',
    color: 'var(--nm-ink)',
    edge: { corner: 'tr', species: 'ink' },
    showBubble: true,
  },
  'own-lilac': {
    align: 'right',
    bg: 'var(--nm-own-lilac)',
    color: 'var(--nm-ink)',
    edge: { corner: 'tr', species: 'overlap' },
    showBubble: true,
  },
  system: {
    align: 'center',
    bg: 'transparent',
    color: 'var(--nm-ink50)',
    showBubble: false,
    extraClass: 'text-xs uppercase tracking-wider',
  },
  'tool-result': {
    align: 'left',
    bg: 'var(--nm-paper-warm)',
    color: 'var(--nm-ink)',
    showBubble: true,
    extraClass: 'font-mono text-xs',
  },
  error: {
    align: 'left',
    bg: 'var(--color-error)',
    color: 'white',
    edge: { corner: 'tl', species: 'error' },
    showBubble: true,
  },
};

export function MessageBubble({
  variant,
  children,
  ariaLabel,
  className,
}: MessageBubbleProps) {
  const v = VARIANT[variant];
  const isCentered = v.align === 'center';
  const isRight = v.align === 'right';

  if (!v.showBubble) {
    return (
      <div
        data-nm="message-bubble"
        data-variant={variant}
        role="note"
        aria-label={ariaLabel}
        className={cn(
          'w-full text-center py-2',
          v.extraClass,
          className
        )}
        style={{ color: v.color }}
      >
        {children}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex',
        isRight ? 'justify-end' : isCentered ? 'justify-center' : 'justify-start'
      )}
    >
      <div
        data-nm="message-bubble"
        data-variant={variant}
        role="note"
        aria-label={ariaLabel}
        className={cn(
          'relative inline-block max-w-[80%] px-3.5 py-2.5 rounded-[var(--radius-lg)]',
          v.extraClass,
          className
        )}
        style={{
          background: v.bg,
          color: v.color,
          ...(variant === 'own' || variant === 'own-lilac'
            ? { boxShadow: 'var(--nm-elev-1)' }
            : {}),
        }}
      >
        {v.edge && <BracketEdge corner={v.edge.corner} species={v.edge.species} />}
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BubbleGroup — auto-spaced container managing sameGap / turnGap
// ---------------------------------------------------------------------------
export interface BubbleGroupProps {
  children: ReactNode;
  /** Vertical gap within a single sender's run (default 4px per NM spec) */
  sameGap?: number;
  /** Vertical gap when sender changes / new turn starts (default 16px) */
  turnGap?: number;
  className?: string;
}

export function BubbleGroup({
  children,
  sameGap = 4,
  turnGap = 16,
  className,
}: BubbleGroupProps) {
  return (
    <div
      data-nm="bubble-group"
      data-same-gap={sameGap}
      data-turn-gap={turnGap}
      className={cn('flex flex-col w-full', className)}
      style={{
        gap: sameGap,
      }}
    >
      {children}
    </div>
  );
}

/**
 * Marker for "this is a new turn" — sits between bubble groups and adds
 * extra spacing equal to (turnGap - sameGap). Composed by callers; not
 * mandatory if they prefer to control gap manually.
 */
export interface TurnBreakProps {
  turnGap?: number;
  sameGap?: number;
}

export function TurnBreak({ turnGap = 16, sameGap = 4 }: TurnBreakProps) {
  return (
    <div
      data-nm="turn-break"
      aria-hidden="true"
      style={{ height: Math.max(0, turnGap - sameGap) }}
    />
  );
}

// ---------------------------------------------------------------------------
// BubbleMetaRow — sender + timestamp row above a bubble
// ---------------------------------------------------------------------------
export interface BubbleMetaRowProps {
  /** Sender display name */
  sender: string;
  /** Species (drives sender color) */
  species: 'carbon' | 'silicon' | 'overlap' | 'neutral';
  /** Formatted timestamp string (e.g. "12:04") */
  time?: string;
  /** Right-align (for own messages) */
  alignRight?: boolean;
  className?: string;
}

const SPECIES_COLOR = {
  carbon: 'var(--color-carbon)',
  silicon: 'var(--color-silicon)',
  overlap: 'var(--color-overlap)',
  neutral: 'var(--nm-ink70)',
};

export function BubbleMetaRow({
  sender,
  species,
  time,
  alignRight,
  className,
}: BubbleMetaRowProps) {
  return (
    <div
      data-nm="bubble-meta-row"
      className={cn(
        'flex items-baseline gap-2 text-xs mb-1',
        alignRight && 'justify-end',
        className
      )}
    >
      <span
        className="font-medium"
        style={{ color: SPECIES_COLOR[species] }}
      >
        {sender}
      </span>
      {time && (
        <span
          style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}
        >
          {time}
        </span>
      )}
    </div>
  );
}
