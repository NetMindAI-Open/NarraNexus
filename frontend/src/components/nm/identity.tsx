/**
 * @file_name: identity.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Identity primitives — Ring Avatar, Group Avatar, Species Dot,
 * Avatar Stack. Implements NM Axiom #3 (ring over fill) and Axiom #1
 * (species color = human/AI identity).
 *
 */

import type { CSSProperties, ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type NMSpecies = 'carbon' | 'silicon' | 'overlap' | 'neutral';
export type NMAvatarSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const SIZE_PX: Record<NMAvatarSize, number> = {
  xs: 24,
  sm: 32,
  md: 40,
  lg: 56,
  xl: 80,
};

const SIZE_FONT: Record<NMAvatarSize, number> = {
  xs: 10,
  sm: 12,
  md: 14,
  lg: 18,
  xl: 26,
};

function speciesColor(species: NMSpecies): string {
  if (species === 'carbon') return 'var(--color-carbon)';
  if (species === 'silicon') return 'var(--color-silicon)';
  if (species === 'overlap') return 'var(--color-overlap)';
  return 'var(--nm-ink50)';
}

// ---------------------------------------------------------------------------
// RingAvatar
// ---------------------------------------------------------------------------
export interface RingAvatarProps {
  species: NMSpecies;
  size?: NMAvatarSize;
  /** Center label (typically 1-2 chars; first letter of name) */
  label: string;
  /** Optional image source. If provided, renders inside the ring instead of label. */
  src?: string;
  alt?: string;
  className?: string;
  title?: string;
  onClick?: () => void;
}

export function RingAvatar({
  species,
  size = 'md',
  label,
  src,
  alt,
  className,
  title,
  onClick,
}: RingAvatarProps) {
  const px = SIZE_PX[size];
  const font = SIZE_FONT[size];
  const ringStyle: CSSProperties = {
    width: px,
    height: px,
    borderColor: speciesColor(species),
    fontSize: font,
  };
  return (
    <div
      data-nm="ring-avatar"
      data-species={species}
      data-size={size}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onClick();
        }
      }}
      title={title ?? label}
      className={cn(
        'inline-flex items-center justify-center select-none rounded-full font-semibold text-[color:var(--nm-ink)] bg-transparent border-2 overflow-hidden',
        onClick && 'cursor-pointer hover:opacity-90 transition-opacity',
        className
      )}
      style={ringStyle}
    >
      {src ? (
        <img src={src} alt={alt ?? label} className="w-full h-full object-cover" />
      ) : (
        <span>{label.slice(0, 2).toUpperCase()}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GroupAvatar — single ring divided into arcs by member species ratio
// ---------------------------------------------------------------------------
export interface GroupAvatarMember {
  species: NMSpecies;
  /** Optional name for hover */
  name?: string;
}

export interface GroupAvatarProps {
  members: GroupAvatarMember[];
  size?: NMAvatarSize;
  /** Center label, e.g. total member count. Defaults to total members. */
  label?: string | number;
  className?: string;
  title?: string;
}

export function GroupAvatar({
  members,
  size = 'md',
  label,
  className,
  title,
}: GroupAvatarProps) {
  const px = SIZE_PX[size];
  const font = SIZE_FONT[size];
  const stroke = 2;
  const r = (px - stroke) / 2;
  const cx = px / 2;
  const cy = px / 2;
  const circumference = 2 * Math.PI * r;

  // Build arc segments
  const total = members.length || 1;
  const segments: { color: string; length: number; offset: number }[] = [];
  let runningOffset = 0;
  members.forEach((m) => {
    const segLen = circumference / total;
    segments.push({
      color: speciesColor(m.species),
      length: segLen,
      offset: -runningOffset, // negative = clockwise
    });
    runningOffset += segLen;
  });

  return (
    <div
      data-nm="group-avatar"
      data-size={size}
      title={title}
      className={cn(
        'inline-flex items-center justify-center select-none rounded-full overflow-visible',
        className
      )}
      style={{ width: px, height: px, position: 'relative' }}
    >
      <svg
        width={px}
        height={px}
        viewBox={`0 0 ${px} ${px}`}
        style={{ transform: 'rotate(-90deg)' }}
        aria-hidden="true"
      >
        {segments.map((s, i) => (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={s.color}
            strokeWidth={stroke}
            strokeDasharray={`${s.length - 1} ${circumference}`}
            strokeDashoffset={s.offset}
            strokeLinecap="butt"
          />
        ))}
      </svg>
      <span
        className="absolute font-semibold"
        style={{
          fontSize: font,
          color: 'var(--nm-ink)',
        }}
      >
        {label ?? members.length}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpeciesDot — small ring dot for "this thing is human/AI/overlap"
// ---------------------------------------------------------------------------
export interface SpeciesDotProps {
  species: NMSpecies;
  /** Diameter in px */
  size?: 6 | 8 | 10 | 12;
  pulse?: boolean;
  /** If false (default) the dot is hollow ring; if true, filled */
  filled?: boolean;
  className?: string;
  title?: string;
}

export function SpeciesDot({
  species,
  size = 8,
  pulse,
  filled,
  className,
  title,
}: SpeciesDotProps) {
  const color = speciesColor(species);
  return (
    <span
      data-nm="species-dot"
      data-species={species}
      title={title}
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
        borderColor: color,
        background: filled ? color : 'transparent',
      }}
      aria-hidden="true"
    />
  );
}

// ---------------------------------------------------------------------------
// BindingDot — the "carbon meets silicon" brand motif
// ---------------------------------------------------------------------------
// A carbon dot · hairline · silicon dot triad, read left-to-right as
// "human binds to AI". Used as the eyebrow/section motif at the top of the
// conversation panel (and onboarding), per the Narra Agent App design ref.
// `pulse` lets the silicon dot breathe while the agent is streaming, so the
// motif doubles as a subtle live-state cue without adding a second indicator.
export interface BindingDotProps {
  /** Diameter of each end dot, px (the connecting hairline scales with it) */
  size?: number;
  /** Pulse the silicon (AI) dot — e.g. while a turn is streaming */
  pulse?: boolean;
  className?: string;
  title?: string;
}

export function BindingDot({ size = 7, pulse, className, title = 'Carbon meets silicon' }: BindingDotProps) {
  const dot = (color: string): CSSProperties => ({
    width: size,
    height: size,
    borderRadius: 9999,
    background: color,
    boxShadow: '0 0.5px 1.5px rgba(42,38,32,0.32)',
    flexShrink: 0,
  });
  return (
    <span
      data-nm="binding-dot"
      title={title}
      aria-hidden="true"
      className={cn('inline-flex items-center', className)}
      style={{ gap: Math.round(size * 0.7) }}
    >
      <span style={dot('var(--color-carbon)')} />
      <span style={{ width: Math.round(size * 1.4), height: 1, background: 'var(--nm-ink30)', flexShrink: 0 }} />
      <span className={cn(pulse && 'animate-pulse')} style={dot('var(--color-silicon)')} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// AvatarStack — overlapping ring avatars + "+N" overflow
// ---------------------------------------------------------------------------
export interface AvatarStackProps {
  avatars: Pick<RingAvatarProps, 'species' | 'label' | 'src' | 'alt'>[];
  size?: NMAvatarSize;
  max?: number;
  className?: string;
  overlap?: number;
}

export function AvatarStack({
  avatars,
  size = 'sm',
  max = 3,
  overlap,
  className,
}: AvatarStackProps) {
  const overlapPx = overlap ?? Math.round(SIZE_PX[size] * 0.35);
  const visible = avatars.slice(0, max);
  const extra = Math.max(0, avatars.length - max);
  return (
    <div
      data-nm="avatar-stack"
      className={cn('inline-flex items-center', className)}
    >
      {visible.map((a, i) => (
        <div
          key={i}
          style={{ marginLeft: i === 0 ? 0 : -overlapPx, zIndex: visible.length - i }}
          className="relative"
        >
          <RingAvatar {...a} size={size} />
        </div>
      ))}
      {extra > 0 && (
        <div
          style={{ marginLeft: -overlapPx, zIndex: 0 }}
          className="relative"
        >
          <RingAvatar species="neutral" size={size} label={`+${extra}`} />
        </div>
      )}
    </div>
  );
}

/**
 * Convenience helper to wrap a child node with a small status dot at
 * bottom-right (e.g., online/offline indicator on an avatar).
 */
export interface AvatarWithStatusProps {
  children: ReactNode;
  status: 'success' | 'warning' | 'error' | 'info' | 'neutral';
  className?: string;
}

export function AvatarWithStatus({
  children,
  status,
  className,
}: AvatarWithStatusProps) {
  const color =
    status === 'success'
      ? 'var(--color-success)'
      : status === 'warning'
      ? 'var(--color-warning)'
      : status === 'error'
      ? 'var(--color-error)'
      : status === 'info'
      ? 'var(--color-info)'
      : 'var(--nm-ink30)';
  return (
    <span
      data-nm="avatar-with-status"
      className={cn('relative inline-block align-middle', className)}
    >
      {children}
      <span
        className="absolute"
        style={{
          right: 0,
          bottom: 0,
          width: 10,
          height: 10,
          background: color,
          borderRadius: '50%',
          border: '2px solid var(--nm-card)',
        }}
        aria-label={`Status: ${status}`}
      />
    </span>
  );
}
