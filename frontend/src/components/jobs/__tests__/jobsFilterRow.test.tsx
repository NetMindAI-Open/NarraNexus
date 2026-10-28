/**
 * @file_name: jobsFilterRow.test.tsx
 * @date: 2026-08-27
 * @description: Regression guard for the Jobs panel's chrome density.
 *
 * History this file has to keep honouring:
 *
 * 2026-07-30 — the 11 status chips were one `whitespace-nowrap` row inside a
 * `ScrollArea horizontal hideScrollbar`. In zh they measure ~660px while the
 * panel's usual home is a 300-440px drawer, so the row cut off after ~7 chips
 * with no scrollbar and no hint the rest existed.
 *
 * 2026-08-27 — the density rebuild fixed the same problem at its source: the
 * row is derived from the data, so a status with no jobs has no chip. The old
 * assertion ("every one of the 11 chips renders") is now the *bug*, because
 * 7-9 of those chips could only ever produce an empty list. These tests pin
 * the new contract instead:
 *
 *   1. chips exist for exactly the statuses that have jobs, plus `All`,
 *   2. each chip carries its count,
 *   3. no jobs → no filter row at all,
 *   4. the row still wraps (nothing is ever clipped away invisibly), and
 *   5. the meter band only renders when it has a distribution to draw.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Job, JobStatus } from '@/types/api';

/** Mutable so each test can set the panel's data before rendering. */
let JOBS: Job[] = [];

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_me', userId: 'user_me' }),
  usePreloadStore: () => ({
    jobs: JOBS,
    jobsLoading: false,
    refreshJobs: vi.fn(),
  }),
}));

import { JobsPanel } from '../JobsPanel';

let seq = 0;
function job(status: JobStatus, extra: Partial<Job> = {}): Job {
  seq += 1;
  return {
    job_id: `job_${seq}`,
    agent_id: 'agent_me',
    user_id: 'user_me',
    job_type: 'scheduled',
    title: `Job ${seq}`,
    status,
    ...extra,
  } as Job;
}

/** The chip row is the `All` chip's parent. */
function filterRow(): HTMLElement | null {
  const all = screen.queryByRole('button', { name: /^All\b/ });
  return all ? (all.parentElement as HTMLElement) : null;
}

beforeEach(() => {
  JOBS = [];
  seq = 0;
});

describe('JobsPanel — status filter row is derived from the data', () => {
  it('renders a chip only for statuses that actually have jobs', () => {
    JOBS = [job('pending'), job('completed'), job('completed')];
    render(<JobsPanel embedded />);

    expect(screen.getByRole('button', { name: /^All\b/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Pending\b/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Completed\b/ })).toBeInTheDocument();

    // The seven statuses with no jobs get no chip. Rendering them was the old
    // behaviour and each was a button whose only outcome was an empty list.
    for (const absent of ['Running', 'Paused', 'No quota', 'Retrying', 'Dep failed', 'Cancelled', 'Failed']) {
      expect(screen.queryByRole('button', { name: new RegExp(`^${absent}\\b`) })).toBeNull();
    }
  });

  it('carries the count on every chip, with the total on All', () => {
    JOBS = [job('completed'), job('completed'), job('failed')];
    render(<JobsPanel embedded />);

    expect(screen.getByRole('button', { name: /^All\s+3$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Completed\s+2$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Failed\s+1$/ })).toBeInTheDocument();
  });

  it('renders no filter row at all when there are no jobs', () => {
    JOBS = [];
    render(<JobsPanel embedded />);
    expect(filterRow()).toBeNull();
  });

  it('still wraps rather than hiding chips behind an invisible scrollbar', () => {
    JOBS = [job('pending'), job('running'), job('completed'), job('failed')];
    render(<JobsPanel embedded />);
    const row = filterRow() as HTMLElement;

    expect(row.className).toContain('flex-wrap');
    // The 2026-07-30 clipping came from this viewport; the row must stay out.
    expect(row.closest('[data-radix-scroll-area-viewport]')).toBeNull();
  });

  it('leads with the attention statuses so failures are one glance away', () => {
    JOBS = [job('completed'), job('completed'), job('running'), job('failed')];
    render(<JobsPanel embedded />);
    const labels = Array.from(filterRow()!.querySelectorAll('button')).map(
      (b) => (b.textContent ?? '').trim(),
    );
    expect(labels[0]).toMatch(/^All/);
    expect(labels[1]).toMatch(/^Failed/);
  });
});

describe('JobsPanel — the meter band only renders when it has something to say', () => {
  const meter = () => document.querySelector('[data-nm="job-status-meter"]');

  it('is absent for an empty panel', () => {
    JOBS = [];
    render(<JobsPanel embedded />);
    expect(meter()).toBeNull();
  });

  it('is absent for a handful of healthy jobs — there is no distribution yet', () => {
    JOBS = [job('pending'), job('running'), job('completed')];
    render(<JobsPanel embedded />);
    expect(meter()).toBeNull();
  });

  it('appears once the list is big enough to have a shape', () => {
    JOBS = [job('pending'), job('running'), job('completed'), job('completed')];
    render(<JobsPanel embedded />);
    expect(meter()).not.toBeNull();
  });

  it('appears for a single failure, however small the list', () => {
    JOBS = [job('failed')];
    render(<JobsPanel embedded />);
    expect(meter()).not.toBeNull();
  });
});

describe('JobsPanel — the job row leads with schedule, not a truncated description', () => {
  it('shows the schedule and next run instead of the description', () => {
    JOBS = [
      job('pending', {
        title: 'Daily check-in',
        description: 'Once a day, drop by with a fresh topic. Pause or cancel any time.',
        trigger_config: { cron: '0 9 * * *' },
        next_run_at: '2099-01-01T09:00:00',
      }),
    ];
    render(<JobsPanel embedded />);

    expect(screen.getByText('Daily check-in')).toBeInTheDocument();
    expect(screen.getByText(/Daily 09:00/)).toBeInTheDocument();
    // The description belongs to the expanded detail now; in the collapsed row
    // it only ever showed a fragment.
    expect(screen.queryByText(/drop by with a fresh topic/)).toBeNull();
  });
});
