/**
 * @file_name: backendTs.test.ts
 * @description: parseBackendTs — the one rule for backend timestamps
 * (review #349 I1). The naive shape is what cloud MySQL actually emits;
 * a test that only feeds 'Z' strings is permanently green regardless of
 * the parsing rule.
 */
import { describe, expect, it } from 'vitest';
import { parseBackendTs } from '../backendTs';

describe('parseBackendTs', () => {
  it('reads an offset-less (naive-UTC) string as UTC, not local time', () => {
    expect(parseBackendTs('2026-08-14T16:00:41.109740')).toBe(
      Date.parse('2026-08-14T16:00:41.109740Z'),
    );
  });

  it('trusts an explicit offset as-is — never blind-appends Z', () => {
    expect(parseBackendTs('2026-08-14T16:00:41+00:00')).toBe(
      Date.parse('2026-08-14T16:00:41Z'),
    );
    expect(parseBackendTs('2026-08-14T17:00:41+01:00')).toBe(
      Date.parse('2026-08-14T16:00:41Z'),
    );
    expect(parseBackendTs('2026-08-14T16:00:41Z')).toBe(
      Date.parse('2026-08-14T16:00:41Z'),
    );
  });

  it('absent or garbage input yields NaN', () => {
    expect(parseBackendTs(null)).toBeNaN();
    expect(parseBackendTs(undefined)).toBeNaN();
    expect(parseBackendTs('')).toBeNaN();
    expect(parseBackendTs('not a date')).toBeNaN();
  });
});
