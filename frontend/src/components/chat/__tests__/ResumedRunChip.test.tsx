/**
 * @file_name: ResumedRunChip.test.tsx
 * @description: The "resumed ongoing run" badge (Shenzhen-r2 B1): after a
 * refresh mid-run, the replay must be labeled as the SAME run continuing,
 * with elapsed anchored to the run's real start — not the reconnect moment.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import ResumedRunChip from '../ResumedRunChip';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { minutes?: number }) =>
      opts?.minutes !== undefined ? `${key}:${opts.minutes}` : key,
  }),
}));

describe('ResumedRunChip', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows elapsed anchored to the run start, not the mount moment', () => {
    const now = Date.parse('2026-08-14T16:08:44Z');
    vi.setSystemTime(now);
    render(<ResumedRunChip startedAtMs={Date.parse('2026-08-14T16:00:41Z')} />);
    // 8 minutes into the run at mount — the whole point of the badge
    expect(screen.getByText('chat.execution.resumedElapsed:8')).toBeTruthy();
  });

  it('ticks while mounted and clamps a sub-minute (or future-skewed) start to 1', () => {
    const now = Date.parse('2026-08-14T16:00:50Z');
    vi.setSystemTime(now);
    render(<ResumedRunChip startedAtMs={Date.parse('2026-08-14T16:00:41Z')} />);
    expect(screen.getByText('chat.execution.resumedElapsed:1')).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(2 * 60 * 1000);
    });
    expect(screen.getByText('chat.execution.resumedElapsed:2')).toBeTruthy();
  });
});
