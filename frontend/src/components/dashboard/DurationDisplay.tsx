/**
 * @file_name: DurationDisplay.tsx
 * @description: Format elapsed time since `startedAt` as Xs / Xm / XhYm.
 *
 * `Date.now()` is strictly impure from React's idempotency rules, but the
 * dashboard re-renders on every poll tick (3s or 30s), so the displayed
 * duration refreshes naturally. We don't re-tick internally to avoid
 * spamming state updates per card. `useSyncExternalStore` would be overkill
 * for a purely cosmetic counter.
 */

const EM_DASH = '\u2014';

// eslint-disable-next-line react-refresh/only-export-components
export function formatDuration(totalSec: number | null): string {
  if (totalSec === null || totalSec === undefined) return EM_DASH;
  const s = Math.max(0, Math.floor(totalSec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h${mm}m`;
}

export function DurationDisplay({ startedAt }: { startedAt: string | null }) {
  if (!startedAt) return <span>{EM_DASH}</span>;
  // eslint-disable-next-line react-hooks/purity -- see module docstring
  const sec = (Date.now() - new Date(startedAt).getTime()) / 1000;
  return <span>{formatDuration(sec)}</span>;
}
