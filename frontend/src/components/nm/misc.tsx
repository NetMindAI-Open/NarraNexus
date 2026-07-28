/**
 * @file_name: misc.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Miscellaneous primitives — Chip, Tag, Badge, CodeBlock, Kbd, Link.
 *
 */

import { Fragment, useState, type AnchorHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { NMSpecies } from './identity';

function speciesColor(species: NMSpecies | 'ink' = 'ink'): string {
  switch (species) {
    case 'carbon':
      return 'var(--color-carbon)';
    case 'silicon':
      return 'var(--color-silicon)';
    case 'overlap':
      return 'var(--color-overlap)';
    case 'neutral':
      return 'var(--nm-ink50)';
    default:
      return 'var(--nm-ink)';
  }
}

// ---------------------------------------------------------------------------
// Chip — hairline pill + optional dismiss
// ---------------------------------------------------------------------------
export interface ChipProps {
  children: ReactNode;
  species?: NMSpecies | 'ink';
  /** When provided, renders an x button that calls this on click */
  onDismiss?: () => void;
  /** Optional leading icon */
  leading?: ReactNode;
  className?: string;
}

export function Chip({
  children,
  species = 'ink',
  onDismiss,
  leading,
  className,
}: ChipProps) {
  const c = speciesColor(species);
  return (
    <span
      data-nm="chip"
      data-species={species}
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[var(--radius-xs)] text-xs',
        className
      )}
      style={{
        border: `1px solid ${c}`,
        color: c,
        background: 'transparent',
      }}
    >
      {leading}
      <span>{children}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Remove"
          className="opacity-60 hover:opacity-100 transition-opacity"
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M2 2L8 8M8 2L2 8" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tag — smaller data chip (no border, just ink-50 on paper-warm)
// ---------------------------------------------------------------------------
export interface TagProps {
  children: ReactNode;
  className?: string;
}

export function Tag({ children, className }: TagProps) {
  return (
    <span
      data-nm="tag"
      className={cn(
        'inline-block px-1.5 py-0.5 rounded-[var(--radius-xs)] text-[10px] uppercase tracking-wider',
        className
      )}
      style={{
        fontFamily: 'var(--font-mono)',
        background: 'var(--nm-paper-warm)',
        color: 'var(--nm-ink50)',
      }}
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Badge — count indicator (typically on icon)
// ---------------------------------------------------------------------------
export interface BadgeProps {
  count: number;
  max?: number;
  species?: NMSpecies | 'ink';
  /** Show as a dot when count==0 isn't relevant */
  dot?: boolean;
  className?: string;
}

export function Badge({
  count,
  max = 99,
  species = 'ink',
  dot = false,
  className,
}: BadgeProps) {
  if (count <= 0 && !dot) return null;
  const c = speciesColor(species);
  if (dot) {
    return (
      <span
        data-nm="badge"
        data-dot="true"
        className={cn('inline-block rounded-full', className)}
        style={{
          width: 8,
          height: 8,
          background: c,
        }}
        aria-label={`${count} unread`}
      />
    );
  }
  const display = count > max ? `${max}+` : String(count);
  return (
    <span
      data-nm="badge"
      className={cn(
        'inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full text-[10px] font-bold',
        className
      )}
      style={{
        background: c,
        color: c === 'var(--nm-ink)' ? 'var(--nm-paper)' : 'white',
        fontFamily: 'var(--font-mono)',
      }}
      aria-label={`${count} unread`}
    >
      {display}
    </span>
  );
}

// ---------------------------------------------------------------------------
// CodeBlock — SunkenWell + SF Mono + optional copy button
// ---------------------------------------------------------------------------
export interface CodeBlockProps {
  code: string;
  language?: string;
  /** Show copy-to-clipboard button (default true) */
  showCopy?: boolean;
  /** Show line numbers (default false) */
  showLineNumbers?: boolean;
  className?: string;
}

export function CodeBlock({
  code,
  language,
  showCopy = true,
  showLineNumbers = false,
  className,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const lines = showLineNumbers ? code.split('\n') : null;
  return (
    <div
      data-nm="code-block"
      data-language={language}
      className={cn(
        'relative group rounded-[var(--radius-sm)] overflow-hidden',
        className
      )}
      style={{
        background: 'var(--nm-paper-warm)',
        boxShadow: 'inset 0 0 0 1px var(--nm-hairline)',
      }}
    >
      {(language || showCopy) && (
        <div
          className="flex items-center justify-between px-3 py-1.5 text-[10px] uppercase tracking-wider border-b"
          style={{
            fontFamily: 'var(--font-mono)',
            borderColor: 'var(--nm-hairline)',
            color: 'var(--nm-ink50)',
            background: 'transparent',
          }}
        >
          <span>{language ?? 'text'}</span>
          {showCopy && (
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(code);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                } catch {
                  /* clipboard API blocked in some sandboxes — no-op */
                }
              }}
              className="text-[10px] uppercase tracking-wider transition-colors hover:text-[color:var(--nm-ink)]"
            >
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          )}
        </div>
      )}
      <pre
        className="m-0 p-3 overflow-x-auto text-xs leading-[1.6]"
        style={{
          fontFamily: 'var(--font-mono)',
          color: 'var(--nm-ink)',
        }}
      >
        {showLineNumbers && lines ? (
          <code className="grid grid-cols-[auto_1fr] gap-x-3">
            {lines.map((line, i) => (
              <Fragment key={i}>
                <span
                  className="text-right select-none"
                  style={{ color: 'var(--nm-ink30)' }}
                  aria-hidden="true"
                >
                  {i + 1}
                </span>
                <span>{line || ' '}</span>
              </Fragment>
            ))}
          </code>
        ) : (
          <code>{code}</code>
        )}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kbd — `[ Cmd ][ K ]` bracket-wrapped key hints
// ---------------------------------------------------------------------------
export interface KbdProps {
  /** Array of key names, each rendered in its own bracketed key cap */
  keys: string[];
  separator?: ReactNode;
  className?: string;
}

export function Kbd({ keys, separator = '+', className }: KbdProps) {
  return (
    <span
      data-nm="kbd"
      className={cn('inline-flex items-center gap-1', className)}
      style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink70)' }}
    >
      {keys.map((k, i) => (
        <Fragment key={`${k}-${i}`}>
          {i > 0 && <span aria-hidden="true" style={{ color: 'var(--nm-ink30)' }}>{separator}</span>}
          <kbd
            className="inline-block px-1.5 py-0.5 text-[10px] uppercase tracking-wider"
            style={{
              border: '1px solid var(--nm-hairline)',
              borderBottomWidth: 2,
              borderRadius: 'var(--radius-xs)',
              background: 'var(--nm-paper-warm)',
              color: 'var(--nm-ink)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {k}
          </kbd>
        </Fragment>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Link — inline ink-underlined link with hover invert
// ---------------------------------------------------------------------------
export interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  external?: boolean;
  children: ReactNode;
}

export function Link({
  external,
  className,
  children,
  ...rest
}: LinkProps) {
  return (
    <a
      data-nm="link"
      target={external ? '_blank' : undefined}
      rel={external ? 'noopener noreferrer' : undefined}
      className={cn(
        'font-medium underline underline-offset-2 decoration-1 transition-colors',
        'hover:bg-[color:var(--nm-ink)] hover:text-[color:var(--nm-paper)] hover:decoration-transparent',
        className
      )}
      style={{ color: 'var(--nm-ink)' }}
      {...rest}
    >
      {children}
      {external && (
        <svg
          className="inline-block ml-0.5 -mt-0.5"
          width="10"
          height="10"
          viewBox="0 0 10 10"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <path d="M3.5 2H8V6.5M8 2L3 7M2 8L8 8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </a>
  );
}
