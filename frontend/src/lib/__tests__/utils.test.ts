/**
 * @file_name: utils.test.ts
 * @description: Behavior contract for shared formatting helpers.
 *
 * Currently pins `formatChatTimestamp` — the IM-sidebar-style date/time
 * formatter used by AgentList. Symptom this guards against (P0 2026-05-27):
 * the sidebar previously showed only `HH:MM:SS`, so a message from three
 * days ago and a message from this morning looked identical. The new
 * formatter is calendar-aware — see the cases below.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { formatChatTimestamp } from '../utils';

// Anchor "now" so the relative cases stay deterministic.
// 2026-05-27 14:30 UTC, which is a Wednesday.
const NOW = new Date('2026-05-27T14:30:00Z');

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('formatChatTimestamp', () => {
  test('same calendar day → time only (HH:MM, no seconds)', () => {
    // 4 hours earlier same UTC day
    const out = formatChatTimestamp('2026-05-27T10:15:00Z');
    expect(out).toMatch(/^\d{2}:\d{2}$/);
    expect(out).not.toMatch(/:\d{2}:\d{2}$/); // no seconds
  });

  test('previous calendar day → "Yesterday"', () => {
    // Midday UTC the day before NOW — far from the date boundary so the
    // "previous calendar day" classification holds in local-TZ runners too
    // (the old 23:45Z slid into NOW's day in e.g. UTC+8). NOW = 2026-05-27.
    expect(formatChatTimestamp('2026-05-26T12:00:00Z')).toBe('Yesterday');
  });

  test('within the past week (2..6 days back) → weekday', () => {
    // 3 days back from 2026-05-27 (Wed) → 2026-05-24 (Sun)
    const out = formatChatTimestamp('2026-05-24T09:00:00Z');
    // en-US short weekday labels
    expect(out).toMatch(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$/);
  });

  test('older than a week, same year → "Mon DD"', () => {
    // 30 days back, still in 2026
    const out = formatChatTimestamp('2026-04-27T09:00:00Z');
    // e.g. "Apr 27"
    expect(out).toMatch(/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}$/);
    expect(out).not.toMatch(/^\d{4}/); // no year prefix
  });

  test('previous calendar year → "YYYY/MM/DD"', () => {
    const out = formatChatTimestamp('2025-12-15T09:00:00Z');
    expect(out).toMatch(/^2025[/-]\d{2}[/-]\d{2}$/);
  });

  test('accepts unix milliseconds too', () => {
    const sameDayMs = new Date('2026-05-27T11:00:00Z').getTime();
    const out = formatChatTimestamp(sameDayMs);
    expect(out).toMatch(/^\d{2}:\d{2}$/);
  });

  test('treats naive (no-TZ) backend timestamps as UTC', () => {
    // Backend writes "2026-05-27 10:00:00" — must NOT be parsed as local
    // (which would shift the calendar day in non-UTC environments and
    // could flip "today" → "yesterday"). parseUTCTimestamp normalises this.
    const out = formatChatTimestamp('2026-05-27 10:00:00');
    // Same calendar day in UTC → HH:MM
    expect(out).toMatch(/^\d{2}:\d{2}$/);
  });
});
