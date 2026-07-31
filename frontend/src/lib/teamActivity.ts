/**
 * @file_name: teamActivity.ts
 * @author:
 * @date: 2026-07-28
 * @description: Pure vocabulary for team-room member activity.
 *
 * The team chat renders the same four states in three places (console summary,
 * console row, in-timeline bubble). Keeping the ordering, duration maths and
 * i18n key mapping here — rather than inline in the components — means the
 * three surfaces can never disagree about what "stalled" looks like, and the
 * logic is unit-testable without rendering anything.
 *
 * Note on `stalled`: it is NOT a cosmetic variant of `queued`. A queued turn
 * has not started; a stalled turn started and then went quiet. Presenting both
 * as "queued" is precisely what let a wedged worker read as a busy one.
 */

import type {
  TeamMemberActivity,
  TeamMemberStatus,
} from '@/types/teams';

/** Sort order for the console: what needs attention first, idle last. */
const STATUS_RANK: Record<TeamMemberStatus, number> = {
  stalled: 0,
  running: 1,
  queued: 2,
  idle: 3,
};

export interface StatusTone {
  /** CSS colour for the dot / accent. */
  color: string;
  /** i18n key for the short label shown next to the name. */
  labelKey: string;
  /** i18n key for the one-paragraph explanation shown when expanded. */
  hintKey: string;
}

export const STATUS_TONES: Record<TeamMemberStatus, StatusTone> = {
  running: {
    color: 'var(--color-silicon)',
    labelKey: 'chat.team.activity.running',
    hintKey: 'chat.team.activity.runningHint',
  },
  queued: {
    // The semantic aliases, not raw palette entries: they are the ones the
    // dark theme re-points at the lighter 400-series.
    color: 'var(--color-warning)',
    labelKey: 'chat.team.activity.queued',
    hintKey: 'chat.team.activity.queuedHint',
  },
  stalled: {
    color: 'var(--color-error)',
    labelKey: 'chat.team.activity.stalled',
    hintKey: 'chat.team.activity.stalledHint',
  },
  idle: {
    color: 'var(--nm-subtle)',
    labelKey: 'chat.team.activity.idle',
    hintKey: 'chat.team.activity.idleHint',
  },
};

/** Parse an ISO timestamp to epoch ms, or null when absent/unparseable. */
export function toMs(iso?: string | null): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Duration + recency of an idle member's last finished turn — the roster's
 * "ran 3m12s · 5m ago" line. Null when it has never run (no finished_at).
 *
 * `durationMs` is null when started_at is absent (legacy rows): the turn DID
 * run for an unknown time, and rendering that as "0s" is a wrong number, not
 * a safe default. Clock-skewed end<start still clamps to 0.
 */
export function lastRunSummary(
  a: TeamMemberActivity,
  now: number,
): { durationMs: number | null; agoMs: number } | null {
  const end = toMs(a.finished_at);
  if (end === null) return null;
  const start = toMs(a.started_at);
  return {
    durationMs: start === null ? null : Math.max(0, end - start),
    agoMs: Math.max(0, now - end),
  };
}

/**
 * Compact duration: `12s` / `3m04s` / `2h11m`.
 *
 * Seconds are dropped past the hour mark — at that scale they are noise, and
 * a long run is a first-class scenario, not an anomaly to count down from.
 */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins < 60) return `${mins}m${String(secs).padStart(2, '0')}s`;
  return `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, '0')}m`;
}

/** Elapsed since an ISO timestamp, or '' when it is absent. */
export function elapsedSince(iso: string | null | undefined, now: number): string {
  const ms = toMs(iso);
  return ms === null ? '' : formatDuration(now - ms);
}

/**
 * Split a stored phase into an i18n key plus its interpolation values.
 *
 * `tool:<name>` is the only parameterised phase; everything else maps to a
 * fixed key. Unknown phases fall back to the generic "working" label rather
 * than leaking a raw internal token into the UI.
 */
export function phaseLabelKey(phase?: string | null): { key: string; values?: Record<string, string> } {
  if (!phase) return { key: 'chat.team.activity.running' };
  if (phase.startsWith('tool:')) {
    return { key: 'chat.team.activity.tool', values: { name: phase.slice(5) } };
  }
  if (phase === 'starting') return { key: 'chat.team.activity.starting' };
  if (phase === 'thinking') return { key: 'chat.team.activity.thinking' };
  if (phase === 'replying') return { key: 'chat.team.activity.replying' };
  return { key: 'chat.team.activity.running' };
}

/** Roster ordering: attention-worthy first, then by name for stability. */
export function compareActivity(
  a: TeamMemberActivity,
  b: TeamMemberActivity,
  nameOf: (agentId: string) => string,
): number {
  const rank = STATUS_RANK[a.status] - STATUS_RANK[b.status];
  if (rank !== 0) return rank;
  return nameOf(a.agent_id).localeCompare(nameOf(b.agent_id));
}
