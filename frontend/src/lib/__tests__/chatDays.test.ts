/**
 * @file_name: chatDays.test.ts
 * @description: Behavior contract for the chat day-separator helpers.
 *
 * Bug: chat history shows only HH:mm:ss per message — a conversation
 * spanning days reads as one continuous stream with no date context.
 * The timeline inserts a day separator whenever the local calendar day
 * changes; today/yesterday get relative labels, older days a locale date.
 */
import { describe, it, expect } from 'vitest';
import { chatDayInfo } from '../chatDays';

const NOW = new Date('2026-07-23T15:00:00');

describe('chatDayInfo', () => {
  it('same-day timestamps share a key; different days differ', () => {
    const morning = new Date('2026-07-23T01:00:00').getTime();
    const evening = new Date('2026-07-23T23:59:00').getTime();
    const prevDay = new Date('2026-07-22T23:59:00').getTime();

    expect(chatDayInfo(morning, NOW).key).toBe(chatDayInfo(evening, NOW).key);
    expect(chatDayInfo(morning, NOW).key).not.toBe(chatDayInfo(prevDay, NOW).key);
  });

  it('classifies today and yesterday', () => {
    expect(chatDayInfo(new Date('2026-07-23T08:00:00').getTime(), NOW).kind).toBe('today');
    expect(chatDayInfo(new Date('2026-07-22T08:00:00').getTime(), NOW).kind).toBe('yesterday');
    expect(chatDayInfo(new Date('2026-07-20T08:00:00').getTime(), NOW).kind).toBe('date');
  });

  it('older days carry a human-readable date label', () => {
    const info = chatDayInfo(new Date('2026-06-01T08:00:00').getTime(), NOW);
    expect(info.kind).toBe('date');
    expect(info.label.length).toBeGreaterThan(0);
    // Must contain the day-of-month so it reads as a date, whatever the locale.
    expect(info.label).toMatch(/1/);
  });

  it('month boundary: May 31 and June 1 are different days', () => {
    const a = chatDayInfo(new Date('2026-05-31T23:00:00').getTime(), NOW);
    const b = chatDayInfo(new Date('2026-06-01T01:00:00').getTime(), NOW);
    expect(a.key).not.toBe(b.key);
  });
});
