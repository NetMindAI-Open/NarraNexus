/**
 * @file_name: narraMemoryLayout.ts
 * @author:
 * @date: 2026-08-06
 * @description: Pure layout math for the Narra Memory timeline.
 *
 * Extracted from NarraMemoryTimeline's useMemo so the geometry is testable
 * (Base recvoAmUUSjKXs "timeline renders wrong"). Three ways a bar could
 * draw outside the axis are fixed here rather than in CSS clipping — a bar
 * poking past "now" reads as broken data, not a styling nit:
 *
 * 1. The minimum visible width used to be added AFTER the left edge was
 *    placed, so a storyline created near "now" (left ≈ 100%) rendered at
 *    up to 100% + min-width. The left edge now yields to keep the bar
 *    inside the axis.
 * 2. A timestamp ahead of the client clock (server/client skew) landed
 *    past 100% outright. Timestamps are clamped to "now" — the axis's
 *    right edge is by definition the present.
 * 3. When every timestamp failed to parse, the fallback placed the bar at
 *    exactly 100% + min-width. Covered by the same clamp.
 *
 * Tick labels are day-granular ("Aug 6") only while the span warrants it;
 * a span inside two days renders four identical day labels — an axis that
 * looks broken — so short spans append time-of-day instead.
 */
import type { MyNarrative } from '@/types';

export const MIN_BAR_WIDTH_PCT = 3;

const TICK_COUNT = 4;
const DAY_MS = 24 * 3600_000;
const MINUTE_MS = 60_000;

const fmtDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const fmtDayTime = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  hour12: false,
});
const fmtDayTimeSec = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

/**
 * Label granularity keys off the gap BETWEEN adjacent ticks, not the total
 * span: a 3-day span puts ticks 18h apart, where day labels collide even
 * though the span exceeds two days; a seconds-old span collides at minute
 * precision. Each tier is finer than the gap it serves.
 */
function tickFormatter(tickGapMs: number): Intl.DateTimeFormat {
  if (tickGapMs < MINUTE_MS) return fmtDayTimeSec;
  if (tickGapMs < DAY_MS) return fmtDayTime;
  return fmtDay;
}

function ts(value: string | null): number | null {
  if (!value) return null;
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : t;
}

export interface TimelineLane {
  n: MyNarrative;
  /** Left edge of the bar, percent of the axis. */
  left: number;
  /** Bar width, percent of the axis; always ≥ MIN_BAR_WIDTH_PCT. */
  width: number;
}

export interface TimelineLayout {
  lanes: TimelineLane[];
  ticks: { left: number; label: string }[];
}

/**
 * Compute the lane/tick geometry for a set of narratives.
 *
 * @param items  narratives from GET /api/me/narratives
 * @param query  raw search text — normalization (trim/lowercase) happens
 *               here, callers pass what the user typed
 * @param now    the axis's right edge (injectable for tests)
 * @returns      null when nothing matches (caller renders the empty state)
 */
export function computeTimelineLayout(
  items: MyNarrative[],
  query: string,
  now: number,
): TimelineLayout | null {
  const q = query.trim().toLowerCase();
  const filtered = items.filter(
    (n) =>
      !q ||
      n.name.toLowerCase().includes(q) ||
      n.summary.toLowerCase().includes(q) ||
      n.topic_hint.toLowerCase().includes(q) ||
      n.agent_name.toLowerCase().includes(q),
  );
  if (filtered.length === 0) return null;

  // The axis's right edge is the present: anything the client clock has not
  // reached yet (server skew, bad data) is drawn AT "now", never past it.
  const clamp = (t: number) => Math.min(t, now);

  const stamps = filtered
    .flatMap((n) => [ts(n.created_at), ts(n.updated_at)])
    .filter((t): t is number => t !== null)
    .map(clamp);
  const min = stamps.length ? Math.min(...stamps) : now;
  // Pad the left edge a touch so the earliest bar isn't flush to 0%.
  const start = min - (now - min) * 0.04 - 1;
  const span = Math.max(now - start, 1);
  const pct = (t: number) => ((t - start) / span) * 100;

  const lanes = [...filtered]
    // Same `?? now` fallback as the positioning below: an unparseable
    // created_at sorts last AND draws at the right edge — one story,
    // not a lane that lists first but renders at the axis end.
    .sort((a, b) => (ts(a.created_at) ?? now) - (ts(b.created_at) ?? now))
    .map((n) => {
      const c = clamp(ts(n.created_at) ?? now);
      const u = Math.max(clamp(ts(n.updated_at) ?? c), c);
      const width = Math.max(pct(u) - pct(c), MIN_BAR_WIDTH_PCT);
      // Left edge yields so the min-width bar never crosses the axis end.
      const left = Math.min(pct(c), 100 - width);
      return { n, left, width };
    });

  const fmt = tickFormatter(span / TICK_COUNT);
  const ticks = Array.from({ length: TICK_COUNT }, (_, i) => {
    const t = start + (span * (i + 0.5)) / TICK_COUNT;
    return { left: pct(t), label: fmt.format(new Date(t)) };
  });

  return { lanes, ticks };
}
