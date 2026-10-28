/**
 * @file_name: AnchoredCoachmark.tsx
 * @author: Bin Liang
 * @date: 2026-08-19
 * @description: Shared coach-mark bubble anchored to a `data-help-id` element.
 * Extracted from MigrationCoachmark when GuideAgentCoachmark became its
 * character-for-character copy — one bubble implementation, N gates/copy.
 *
 * Mechanics (unchanged from the original): measure the anchor via
 * querySelector + getBoundingClientRect, portal to body so scroll containers
 * can't clip it, a 500ms interval only to win the sidebar-mount race (stops
 * once anchored or after ~10s; resize keeps it fresh), render nothing while
 * the anchor is absent (collapsed rail).
 *
 * NEW invariant the extraction adds: ONE bubble per anchor at a time. Two
 * one-shot coachmarks can be armed simultaneously (local first run: the
 * migration nudge AND the new-user guide nudge both point at the sidebar
 * "+"), and both used to render at the exact same fixed coordinates —
 * pixel-perfect overlap that read as a UI glitch. A module-level claim map
 * queues the second: while blocked it does NO DOM work (a Map lookup per
 * tick, no querySelector) and takes over when the holder dismisses/unmounts.
 */

import { useState, useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

// anchor selector -> the token of the instance currently showing there.
const anchorHolders = new Map<string, symbol>();

interface AnchoredCoachmarkProps {
  anchorSelector: string;
  onDismiss: () => void;
  /** Bubble body (already-translated text). */
  children: ReactNode;
  /** Already-translated dismiss-button label. */
  dismissLabel: ReactNode;
}

export function AnchoredCoachmark({
  anchorSelector,
  onDismiss,
  children,
  dismissLabel,
}: AnchoredCoachmarkProps) {
  const tokenRef = useRef<symbol | null>(null);
  if (tokenRef.current === null) tokenRef.current = Symbol('coachmark');
  const token = tokenRef.current;

  const [rect, setRect] = useState<DOMRect | null>(null);
  const [owns, setOwns] = useState(false);

  useEffect(() => {
    let intervalId = 0;
    let attempts = 0;
    const stop = () => {
      if (intervalId) { window.clearInterval(intervalId); intervalId = 0; }
    };
    const tick = () => {
      // Claim the anchor first; while another bubble holds it, stay dormant
      // (cheap: no querySelector) and keep waiting — the holder's unmount
      // releases the claim and the next tick takes over. The give-up counter
      // only runs while we OWN the anchor, so queueing behind a long-lived
      // bubble doesn't eat the 10s mount-race budget.
      const holder = anchorHolders.get(anchorSelector);
      if (holder !== undefined && holder !== token) return;
      if (holder === undefined) {
        anchorHolders.set(anchorSelector, token);
        setOwns(true);
      }
      const el = document.querySelector(anchorSelector) as HTMLElement | null;
      const r = el?.getBoundingClientRect() ?? null;
      // Ignore a zero-size / offscreen anchor (collapsed sidebar, not mounted yet).
      const next = r && r.width > 0 && r.height > 0 ? r : null;
      setRect((prev) => {
        // Skip a redundant render if the geometry is unchanged.
        if (prev && next && prev.top === next.top && prev.right === next.right) return prev;
        return next;
      });
      // The interval only wins the sidebar-mount race. Stop once we have the
      // anchor (resize keeps it fresh), or after ~10s if it never appears
      // (e.g. the sidebar stays collapsed) so we don't poll for the whole session.
      attempts += 1;
      if (next || attempts >= 20) stop();
    };
    tick();
    window.addEventListener('resize', tick);
    intervalId = window.setInterval(tick, 500);
    return () => {
      window.removeEventListener('resize', tick);
      stop();
      if (anchorHolders.get(anchorSelector) === token) {
        anchorHolders.delete(anchorSelector);
      }
    };
  }, [anchorSelector, token]);

  if (!owns || !rect) return null;

  return createPortal(
    <div
      style={{
        position: 'fixed',
        top: rect.top + rect.height / 2,
        left: rect.right + 10,
        transform: 'translateY(-50%)',
        zIndex: 60,
        maxWidth: 240,
      }}
      className={cn(
        'rounded-[var(--radius-sm)] border shadow-md',
        'border-[var(--nm-ink)] bg-[var(--nm-paper)] px-3 py-2',
      )}
    >
      {/* left-pointing arrow toward the anchor */}
      <span
        className="absolute h-2 w-2 rotate-45 border-b border-l bg-[var(--nm-paper)] border-[var(--nm-ink)]"
        style={{ left: -5, top: '50%', marginTop: -4 }}
      />
      <div className="text-xs text-[var(--nm-ink)]">{children}</div>
      <button
        onClick={onDismiss}
        className="mt-1.5 text-[11px] font-medium text-[var(--nm-ink)] underline"
      >
        {dismissLabel}
      </button>
    </div>,
    document.body,
  );
}
