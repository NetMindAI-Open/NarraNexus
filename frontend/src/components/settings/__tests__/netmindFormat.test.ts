/**
 * @file netmindFormat.test.ts
 * @description Unit tests for the shared formatting helpers used by the
 * Account & Subscription panel (money display, free-tier percentage, plan
 * period, date). Pure functions — no rendering.
 */

import { describe, it, expect } from 'vitest';
import { money, freeTierPctLeft, formatPeriod, formatDate } from '../netmindFormat';
import type { QuotaMeResponse } from '@/types';

describe('money', () => {
  it('truncates NetMind 4-decimal strings to 2', () => {
    expect(money('9.9300')).toBe('9.93');
  });
  it('accepts numbers', () => {
    expect(money(19)).toBe('19.00');
  });
  it('falls back to em-dash on null/empty/garbage', () => {
    expect(money(null)).toBe('—');
    expect(money(undefined)).toBe('—');
    expect(money('')).toBe('—');
    expect(money('abc')).toBe('—');
  });
});

describe('freeTierPctLeft', () => {
  const active = (over: Record<string, unknown> = {}) =>
    ({
      enabled: true,
      status: 'active',
      remaining_input_tokens: 124_000,
      remaining_output_tokens: 119_000,
      initial_input_tokens: 200_000,
      initial_output_tokens: 150_000,
      granted_input_tokens: 0,
      granted_output_tokens: 0,
      used_input_tokens: 76_000,
      used_output_tokens: 31_000,
      prefer_system_override: true,
      ...over,
    }) as unknown as QuotaMeResponse;

  it('takes the MORE depleted of input/output (honest ceiling)', () => {
    // input 62% left, output ~79% left → 62
    expect(freeTierPctLeft(active())).toBe(62);
  });
  it('exhausted → 0', () => {
    expect(freeTierPctLeft(active({ status: 'exhausted' }))).toBe(0);
  });
  it('no bar when feature is off / uninitialized / fetch failed', () => {
    expect(freeTierPctLeft(null)).toBeNull();
    expect(freeTierPctLeft({ enabled: false } as QuotaMeResponse)).toBeNull();
    expect(
      freeTierPctLeft({ enabled: true, status: 'uninitialized' } as QuotaMeResponse),
    ).toBeNull();
  });
  it('zero totals count as untouched (ratio 1), not division crash', () => {
    expect(
      freeTierPctLeft(active({ initial_input_tokens: 0, remaining_input_tokens: 0 })),
    ).toBe(79);
  });
});

describe('formatPeriod', () => {
  it('maps "month" to the localized short label', () => {
    expect(formatPeriod('month', 'mo')).toBe('mo');
  });
  it('passes unexpected periods through verbatim (dev drifts to "2day")', () => {
    expect(formatPeriod('2day', 'mo')).toBe('2day');
  });
  it('missing period falls back to the month label', () => {
    expect(formatPeriod(undefined, 'mo')).toBe('mo');
  });
});

describe('formatDate', () => {
  it('renders unix seconds as YYYY-MM-DD', () => {
    expect(formatDate(1790000000)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
  it('never throws on garbage input', () => {
    expect(formatDate(Number.NaN)).toBe('—');
  });
});
