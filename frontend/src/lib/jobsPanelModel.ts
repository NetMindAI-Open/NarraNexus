/**
 * @file_name: jobsPanelModel.ts
 * @author:
 * @date: 2026-08-27
 * @description: Pure view-model for the Jobs panel — what each band should show.
 *
 * The density rebuild replaced "always render every band" with "a band renders
 * only when the data it carries is non-empty". Those conditions (which filter
 * chips exist, whether the meter earns its row, what the second line of a row
 * says) are the whole design, so they live here as pure functions rather than
 * inline in JobsPanel.tsx where they cannot be tested.
 *
 * Nothing here touches i18n: `describeRow` returns translation *keys* plus
 * params, and the caller does the `t()`. That keeps the rules assertable
 * without booting an i18n instance.
 */

import type { Job, JobStatus } from '@/types/api';

/**
 * Statuses that mean "a human should look at this". They lead the filter row,
 * lead the meter legend, and give their rows a colored left rail.
 */
export const ATTENTION_STATUSES: readonly JobStatus[] = [
  'failed',
  'blocked_failed',
  'paused_no_quota',
  'paused_spend_cap',
];

/**
 * Canonical status order for the filter chips and the meter legend:
 * attention → in progress → waiting → terminal. One list so the chip row and
 * the legend can never disagree about ordering.
 */
export const STATUS_ORDER: readonly JobStatus[] = [
  // attention
  'failed',
  'blocked_failed',
  'paused_no_quota',
  'paused_spend_cap',
  // in progress
  'running',
  'active',
  'cooling',
  // waiting
  'pending',
  'blocked',
  'paused',
  // terminal
  'completed',
  'cancelled',
];

/** Statuses a job cannot leave on its own. */
const TERMINAL_STATUSES: readonly JobStatus[] = ['completed', 'cancelled', 'failed'];

/** Statuses that count as a failure when computing the success rate. */
const FAILURE_STATUSES: readonly JobStatus[] = ['failed', 'blocked_failed'];

/** The meter is pure chrome below this many jobs — there is no "distribution". */
const METER_MIN_JOBS = 4;

export function isAttentionStatus(status: JobStatus): boolean {
  return ATTENTION_STATUSES.includes(status);
}

export function isTerminalStatus(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function countByStatus(jobs: Job[]): Partial<Record<JobStatus, number>> {
  const counts: Partial<Record<JobStatus, number>> = {};
  for (const j of jobs) {
    counts[j.status] = (counts[j.status] ?? 0) + 1;
  }
  return counts;
}

export interface FilterOption {
  status: JobStatus | 'all';
  count: number;
}

/**
 * The filter chips to render — `all` plus every status that actually has jobs.
 *
 * The old row rendered all 11 statuses unconditionally, so 7-9 of them were
 * buttons whose only possible outcome was an empty list, and in a 300-440px
 * drawer they wrapped to three lines. Deriving the row from the data removes
 * both problems at once. An empty job list yields an empty row: filtering
 * nothing is not a thing a user can want.
 */
export function filterOptions(jobs: Job[]): FilterOption[] {
  if (jobs.length === 0) return [];
  const counts = countByStatus(jobs);
  const present = STATUS_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((status) => ({
    status,
    count: counts[status] as number,
  }));
  return [{ status: 'all' as const, count: jobs.length }, ...present];
}

/**
 * Completed / (completed + failed), or null when nothing has finished yet.
 *
 * Returning null rather than 0 matters: the old strip rendered a flat "0%" for
 * a brand-new agent, which reads as "everything failed" instead of "no data".
 */
export function successRate(jobs: Job[]): number | null {
  const completed = jobs.filter((j) => j.status === 'completed').length;
  const failed = jobs.filter((j) => FAILURE_STATUSES.includes(j.status)).length;
  const decided = completed + failed;
  if (decided === 0) return null;
  return Math.round((completed / decided) * 100);
}

/** Band B renders only when there is a distribution worth drawing. */
export function shouldShowMeter(jobs: Job[]): boolean {
  if (jobs.length === 0) return false;
  if (jobs.some((j) => FAILURE_STATUSES.includes(j.status))) return true;
  return jobs.length >= METER_MIN_JOBS;
}

export interface MeterSegment {
  status: JobStatus;
  count: number;
  /** Share of the total, 0-1. */
  ratio: number;
}

export function meterSegments(jobs: Job[]): MeterSegment[] {
  if (jobs.length === 0) return [];
  const counts = countByStatus(jobs);
  return STATUS_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((status) => ({
    status,
    count: counts[status] as number,
    ratio: (counts[status] as number) / jobs.length,
  }));
}

/* ------------------------------------------------------------------ *
 * Row line 2 — schedule + timing
 * ------------------------------------------------------------------ */

export interface RowSegment {
  key: string;
  params?: Record<string, string | number>;
}

export interface RowMeta {
  /** How this job is scheduled ("Daily 09:00", "Every 15 min", "After 2 jobs"). */
  schedule: RowSegment | null;
  /** When it next runs / how long it has been running / when it last failed. */
  timing: RowSegment | null;
}

export interface DescribeRowOptions {
  /** Injected so the model stays free of i18n and of the wall clock. */
  formatTime: (iso: string) => string;
  now?: Date;
}

/**
 * Parse a job timestamp.
 *
 * `next_run_at` / `last_run_at` / `trigger_config.run_at` follow the v2
 * timezone protocol: a timezone-naive ISO string already expressed in the
 * user's local wall time, paired with an IANA name. JS parses a naive ISO as
 * browser-local, which is what we want — deliberately NOT the UTC-assuming
 * `parseUTCTimestamp` in lib/utils, which is for backend `created_at` columns.
 * (A user whose browser timezone differs from the job's `*_timezone` sees a
 * relative label offset by that difference; the exact stamp plus its IANA name
 * is in the expanded detail.)
 */
function parseJobTime(iso: string): Date {
  return new Date(iso.trim().replace(' ', 'T'));
}

/** Compact elapsed-time readout, e.g. `2m 04s`, `1h 12m`, `3d 4h`. */
export function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  if (min < 60) return `${min}m ${String(totalSec % 60).padStart(2, '0')}s`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ${min % 60}m`;
  return `${Math.floor(hr / 24)}d ${hr % 24}h`;
}

/**
 * Locale-aware relative label that works in BOTH directions.
 *
 * `formatRelativeTime` in lib/utils is English-only and `formatMessageAge`
 * collapses every future timestamp to "now" — but a scheduled job's headline
 * fact is a *future* time, so neither could be reused here.
 */
export function formatRelative(iso: string, locale: string, now: Date = new Date()): string {
  const date = parseJobTime(iso);
  if (!Number.isFinite(date.getTime())) return '';

  let rtf: Intl.RelativeTimeFormat;
  try {
    rtf = new Intl.RelativeTimeFormat(locale || undefined, { numeric: 'auto' });
  } catch {
    rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  }

  const diffSec = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 45) return rtf.format(0, 'second');
  const min = Math.round(diffSec / 60);
  if (Math.abs(min) < 60) return rtf.format(min, 'minute');
  const hr = Math.round(diffSec / 3600);
  if (Math.abs(hr) < 24) return rtf.format(hr, 'hour');
  const day = Math.round(diffSec / 86400);
  if (Math.abs(day) < 30) return rtf.format(day, 'day');
  const month = Math.round(day / 30);
  if (Math.abs(month) < 12) return rtf.format(month, 'month');
  return rtf.format(Math.round(day / 365), 'year');
}

/** `0 9 * * *` → `09:00`; `15 * * * *` → hourly at :15; anything else → null. */
function describeCron(expr: string): RowSegment | null {
  const parts = expr.trim().split(/\s+/);
  if (parts.length < 5) return null;
  const [minute, hour, dom, mon, dow] = parts;
  const everyDay = dom === '*' && mon === '*' && dow === '*';
  if (!everyDay) return null;

  const isNum = (s: string) => /^\d{1,2}$/.test(s);
  if (isNum(minute) && isNum(hour)) {
    return {
      key: 'jobs.row.daily',
      params: { time: `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}` },
    };
  }
  if (isNum(minute) && hour === '*') {
    return { key: 'jobs.row.hourly', params: { minute: minute.padStart(2, '0') } };
  }
  return null;
}

/**
 * Compact interval token: `45s`, `15m`, `2h`, `2d`.
 *
 * A compact token rather than "{{count}} minutes" so the phrasing needs no
 * plural forms — these keys ship in ten locales including Arabic, whose six
 * plural categories would have to be authored (and kept correct) for a string
 * that is a data readout, not prose. Same class as the `15s` already rendered
 * in the expanded detail's configuration block.
 */
export function formatInterval(seconds: number): string {
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function describeInterval(seconds: number): RowSegment {
  return { key: 'jobs.row.every', params: { interval: formatInterval(seconds) } };
}

function describeSchedule(job: Job, formatTime: (iso: string) => string): RowSegment | null {
  const deps = job.depends_on ?? [];
  // A blocked job's headline fact is what it is blocked ON, not its cron.
  // ONE place builds this segment: two returns of the same key once carried
  // different param names ({ n } vs { count }), and the second rendered the
  // raw "{{n}}" placeholder — `count` is also i18next's plural magic key.
  const afterDeps: RowSegment = { key: 'jobs.row.afterDeps', params: { n: deps.length } };
  if (deps.length > 0 && (job.status === 'blocked' || job.status === 'blocked_failed')) {
    return afterDeps;
  }

  const cfg = job.trigger_config;
  if (cfg) {
    if (typeof cfg.interval_seconds === 'number' && cfg.interval_seconds > 0) {
      return describeInterval(cfg.interval_seconds);
    }
    if (typeof cfg.cron === 'string' && cfg.cron.trim()) {
      // A half-understood weekly/monthly rendering is worse than the exact
      // expression, which anyone scheduling jobs can read. Fall back verbatim.
      return describeCron(cfg.cron) ?? { key: 'jobs.row.cron', params: { expr: cfg.cron.trim() } };
    }
    if (typeof cfg.run_at === 'string' && cfg.run_at) {
      return { key: 'jobs.row.once', params: { time: formatTime(cfg.run_at) } };
    }
  }

  if (deps.length > 0) return afterDeps;
  return null;
}

function describeTiming(
  job: Job,
  formatTime: (iso: string) => string,
  now: Date,
): RowSegment | null {
  const running = job.status === 'running' || job.status === 'active';
  if (running && job.last_run_at) {
    const started = parseJobTime(job.last_run_at);
    if (Number.isFinite(started.getTime())) {
      return {
        key: 'jobs.row.elapsed',
        params: { duration: formatDuration(now.getTime() - started.getTime()) },
      };
    }
  }

  if (FAILURE_STATUSES.includes(job.status) && job.last_run_at) {
    return { key: 'jobs.row.failedAt', params: { time: formatTime(job.last_run_at) } };
  }

  if (!isTerminalStatus(job.status) && job.next_run_at) {
    return { key: 'jobs.row.next', params: { time: formatTime(job.next_run_at) } };
  }

  if (job.last_run_at) {
    return { key: 'jobs.row.lastRun', params: { time: formatTime(job.last_run_at) } };
  }

  return null;
}

/**
 * The two halves of a job row's second line.
 *
 * This line replaced the truncated `job.description` (which clipped to things
 * like "Once a day, drop by with a fresh topic. Pause or…" — a sentence
 * fragment carrying no information). The description now opens the expanded
 * detail, where it has room to be a sentence.
 */
export function describeRow(job: Job, opts: DescribeRowOptions): RowMeta {
  const now = opts.now ?? new Date();
  return {
    schedule: describeSchedule(job, opts.formatTime),
    timing: describeTiming(job, opts.formatTime, now),
  };
}
