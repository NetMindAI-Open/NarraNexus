/**
 * @file_name: jobsPanelModel.test.ts
 * @date: 2026-08-27
 * @description: Unit tests for the Jobs panel view-model.
 *
 * The density rebuild moved every "what should this band show" decision out of
 * JobsPanel.tsx and into pure functions, because the new rules are conditional
 * (a band renders only when the data it carries is non-empty) and conditional
 * rendering is exactly what silently regresses. These tests pin the conditions.
 */

import { describe, it, expect } from 'vitest';
import {
  STATUS_ORDER,
  isAttentionStatus,
  countByStatus,
  filterOptions,
  successRate,
  shouldShowMeter,
  meterSegments,
  describeRow,
  formatDuration,
  formatInterval,
  formatRelative,
} from '../jobsPanelModel';
import type { Job, JobStatus } from '@/types/api';

/** Minimal Job factory — only the fields the model reads. */
function job(status: JobStatus, extra: Partial<Job> = {}): Job {
  return {
    job_id: `job_${Math.random().toString(36).slice(2, 8)}`,
    agent_id: 'agt_1',
    user_id: 'usr_1',
    job_type: 'scheduled',
    title: 'T',
    status,
    ...extra,
  } as Job;
}

const NOW = new Date('2026-08-27T12:00:00');
/** Stub formatter so schedule/timing assertions don't depend on Intl output. */
const fmt = (iso: string) => `@${iso}`;

describe('status vocabulary', () => {
  it('orders attention statuses before everything else', () => {
    const attention = STATUS_ORDER.filter(isAttentionStatus);
    const rest = STATUS_ORDER.filter((s) => !isAttentionStatus(s));
    const firstRestIndex = STATUS_ORDER.indexOf(rest[0]);
    for (const s of attention) {
      expect(STATUS_ORDER.indexOf(s)).toBeLessThan(firstRestIndex);
    }
  });

  it('covers every JobStatus exactly once', () => {
    expect(new Set(STATUS_ORDER).size).toBe(STATUS_ORDER.length);
    expect(STATUS_ORDER).toHaveLength(12);
  });

  it('treats failed / dep-failed / no-quota / spend-cap as attention', () => {
    expect(isAttentionStatus('failed')).toBe(true);
    expect(isAttentionStatus('blocked_failed')).toBe(true);
    expect(isAttentionStatus('paused_no_quota')).toBe(true);
    expect(isAttentionStatus('paused_spend_cap')).toBe(true);
    expect(isAttentionStatus('pending')).toBe(false);
    expect(isAttentionStatus('completed')).toBe(false);
  });
});

describe('countByStatus', () => {
  it('counts only the statuses present', () => {
    const counts = countByStatus([job('pending'), job('pending'), job('failed')]);
    expect(counts.pending).toBe(2);
    expect(counts.failed).toBe(1);
    expect(counts.completed).toBeUndefined();
  });
});

describe('filterOptions — the chip row', () => {
  it('renders nothing for an empty job list', () => {
    // Band E: a filter row over zero jobs is pure chrome.
    expect(filterOptions([])).toEqual([]);
  });

  it('omits statuses with no jobs (the whole point of the rebuild)', () => {
    const opts = filterOptions([job('pending')]);
    expect(opts.map((o) => o.status)).toEqual(['all', 'pending']);
  });

  it('always leads with "all" carrying the total', () => {
    const opts = filterOptions([job('completed'), job('failed'), job('running')]);
    expect(opts[0]).toEqual({ status: 'all', count: 3 });
  });

  it('surfaces attention statuses right after "all"', () => {
    const opts = filterOptions([
      job('completed'), job('completed'), job('running'), job('failed'),
    ]);
    expect(opts.map((o) => o.status)).toEqual(['all', 'failed', 'running', 'completed']);
  });

  it('carries per-status counts', () => {
    const opts = filterOptions([job('completed'), job('completed'), job('failed')]);
    expect(opts.find((o) => o.status === 'completed')?.count).toBe(2);
    expect(opts.find((o) => o.status === 'failed')?.count).toBe(1);
  });
});

describe('shouldShowMeter — band B renders only when it has something to say', () => {
  it('hides for an empty list', () => {
    expect(shouldShowMeter([])).toBe(false);
  });

  it('hides below four jobs when nothing failed', () => {
    expect(shouldShowMeter([job('pending')])).toBe(false);
    expect(shouldShowMeter([job('pending'), job('running'), job('completed')])).toBe(false);
  });

  it('shows from four jobs up', () => {
    expect(shouldShowMeter([job('pending'), job('running'), job('completed'), job('pending')])).toBe(true);
  });

  it('shows for any failure, however small the list', () => {
    expect(shouldShowMeter([job('failed')])).toBe(true);
    expect(shouldShowMeter([job('blocked_failed')])).toBe(true);
  });
});

describe('successRate', () => {
  it('is null with no terminal outcome yet — 0% would be a lie', () => {
    expect(successRate([job('pending'), job('running')])).toBeNull();
    expect(successRate([])).toBeNull();
  });

  it('is completed / (completed + failed)', () => {
    const jobs = [
      ...Array.from({ length: 8 }, () => job('completed')),
      job('failed'), job('failed'),
      job('running'),
    ];
    expect(successRate(jobs)).toBe(80);
  });

  it('counts dependency failures as failures', () => {
    expect(successRate([job('completed'), job('blocked_failed')])).toBe(50);
  });
});

describe('meterSegments', () => {
  it('emits only non-empty statuses, in STATUS_ORDER', () => {
    const segs = meterSegments([job('completed'), job('completed'), job('failed'), job('running')]);
    expect(segs.map((s) => s.status)).toEqual(['failed', 'running', 'completed']);
  });

  it('emits ratios that sum to 1', () => {
    const segs = meterSegments([job('completed'), job('failed'), job('running'), job('pending')]);
    const sum = segs.reduce((a, s) => a + s.ratio, 0);
    expect(sum).toBeCloseTo(1, 6);
  });

  it('is empty for an empty list', () => {
    expect(meterSegments([])).toEqual([]);
  });
});

describe('describeRow — schedule half of line 2', () => {
  it('describes an interval with a compact token in the largest whole unit', () => {
    const schedule = (interval_seconds: number) =>
      describeRow(job('pending', { trigger_config: { interval_seconds } }), { now: NOW, formatTime: fmt }).schedule;
    expect(schedule(900)).toEqual({ key: 'jobs.row.every', params: { interval: '15m' } });
    expect(schedule(45)).toEqual({ key: 'jobs.row.every', params: { interval: '45s' } });
    expect(schedule(7200)).toEqual({ key: 'jobs.row.every', params: { interval: '2h' } });
    expect(schedule(172800)).toEqual({ key: 'jobs.row.every', params: { interval: '2d' } });
  });

  it('keeps interval tokens free of plural forms — they ship in ten locales', () => {
    // Arabic alone has six plural categories; a data readout should not need
    // any of them authored.
    expect(formatInterval(60)).toBe('1m');
    expect(formatInterval(1)).toBe('1s');
  });

  it('humanises a plain daily cron', () => {
    expect(describeRow(job('pending', { trigger_config: { cron: '0 9 * * *' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.daily', params: { time: '09:00' } });
    expect(describeRow(job('pending', { trigger_config: { cron: '30 18 * * *' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.daily', params: { time: '18:30' } });
  });

  it('humanises an hourly cron', () => {
    expect(describeRow(job('pending', { trigger_config: { cron: '15 * * * *' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.hourly', params: { minute: '15' } });
  });

  it('falls back to the raw expression for cron shapes it does not model', () => {
    // Deliberate: a half-understood weekly/monthly rendering is worse than the
    // exact expression, which a scheduling user can read.
    expect(describeRow(job('pending', { trigger_config: { cron: '0 8 * * 1' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.cron', params: { expr: '0 8 * * 1' } });
  });

  it('describes a one-off by its run time', () => {
    expect(describeRow(job('pending', { job_type: 'one_off', trigger_config: { run_at: '2026-08-28T09:00:00' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.once', params: { time: '@2026-08-28T09:00:00' } });
  });

  it('describes a blocked job by its dependency count', () => {
    expect(describeRow(job('blocked', { depends_on: ['inst_a', 'inst_b'] }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.afterDeps', params: { n: 2 } });
  });

  it('names the dependency count for an unscheduled dependent job — same params as the blocked form', () => {
    // Regression: this branch passed { count } while the locale string reads
    // {{n}}, so the row showed the literal placeholder.
    expect(describeRow(job('pending', { depends_on: ['inst_a'] }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.afterDeps', params: { n: 1 } });
  });

  it('prefers the schedule over the dependency count while not blocked', () => {
    expect(describeRow(job('pending', { depends_on: ['inst_a'], trigger_config: { cron: '0 9 * * *' } }), { now: NOW, formatTime: fmt }).schedule)
      .toEqual({ key: 'jobs.row.daily', params: { time: '09:00' } });
  });

  it('is null when there is nothing schedulable to say', () => {
    expect(describeRow(job('completed'), { now: NOW, formatTime: fmt }).schedule).toBeNull();
  });
});

describe('describeRow — timing half of line 2', () => {
  it('shows elapsed time while running', () => {
    const j = job('running', { last_run_at: '2026-08-27T11:57:56' });
    expect(describeRow(j, { now: NOW, formatTime: fmt }).timing)
      .toEqual({ key: 'jobs.row.elapsed', params: { duration: '2m 04s' } });
  });

  it('shows when a failure happened', () => {
    const j = job('failed', { last_run_at: '2026-08-24T12:00:00' });
    expect(describeRow(j, { now: NOW, formatTime: fmt }).timing)
      .toEqual({ key: 'jobs.row.failedAt', params: { time: '@2026-08-24T12:00:00' } });
  });

  it('prefers the next run for anything still scheduled', () => {
    const j = job('pending', { next_run_at: '2026-08-28T02:00:00', last_run_at: '2026-08-27T02:00:00' });
    expect(describeRow(j, { now: NOW, formatTime: fmt }).timing)
      .toEqual({ key: 'jobs.row.next', params: { time: '@2026-08-28T02:00:00' } });
  });

  it('falls back to the last run once terminal', () => {
    const j = job('completed', { next_run_at: '2026-08-28T02:00:00', last_run_at: '2026-08-27T02:00:00' });
    expect(describeRow(j, { now: NOW, formatTime: fmt }).timing)
      .toEqual({ key: 'jobs.row.lastRun', params: { time: '@2026-08-27T02:00:00' } });
  });

  it('is null when the job has never run and has nothing scheduled', () => {
    expect(describeRow(job('pending'), { now: NOW, formatTime: fmt }).timing).toBeNull();
  });
});

describe('formatDuration', () => {
  it('pads the seconds so the column does not jitter', () => {
    expect(formatDuration(124_000)).toBe('2m 04s');
  });
  it('drops to whole seconds under a minute', () => {
    expect(formatDuration(9_000)).toBe('9s');
  });
  it('switches to h/m past an hour', () => {
    expect(formatDuration(4_320_000)).toBe('1h 12m');
  });
  it('switches to d/h past a day', () => {
    expect(formatDuration(273_600_000)).toBe('3d 4h');
  });
  it('never renders a negative duration', () => {
    expect(formatDuration(-5000)).toBe('0s');
  });
});

describe('formatRelative', () => {
  it('handles the future — next_run_at is always ahead of now', () => {
    const out = formatRelative('2026-08-28T02:00:00', 'en', NOW);
    expect(out).toMatch(/14 hours|tomorrow/);
  });

  it('handles the past', () => {
    expect(formatRelative('2026-08-24T12:00:00', 'en', NOW)).toMatch(/3 days ago/);
  });

  it('is locale-aware', () => {
    expect(formatRelative('2026-08-24T12:00:00', 'zh', NOW)).toMatch(/天/);
  });

  it('degrades to an empty string on an unparseable timestamp', () => {
    expect(formatRelative('not-a-date', 'en', NOW)).toBe('');
  });

  it('survives an unknown locale tag instead of throwing', () => {
    expect(() => formatRelative('2026-08-24T12:00:00', 'zz-ZZ-nonsense', NOW)).not.toThrow();
  });
});
