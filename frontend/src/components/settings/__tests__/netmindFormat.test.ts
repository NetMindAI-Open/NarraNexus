/**
 * @file netmindFormat.test.ts
 * @description Unit tests for the shared formatting helpers used by the
 * Account & Subscription panel (money display, free-tier percentage, plan
 * period, date). Pure functions — no rendering.
 */

import { describe, it, expect, test } from 'vitest';
import { money, creditMoney, freeTierPctLeft, freeTierCreditLeft, formatPeriod, formatDate } from '../netmindFormat';
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
      currency: 'USD',
      max_budget: 10,
      spend: 3.8,
      remaining: 6.2,
      ...over,
    }) as unknown as QuotaMeResponse;

  it('is the share of the wallet still unspent', () => {
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
  it('a zero-budget wallet has no bar rather than dividing by zero', () => {
    expect(freeTierPctLeft(active({ max_budget: 0, remaining: 0 }))).toBeNull();
  });
  it('clamps an overspent wallet to 0 instead of going negative', () => {
    expect(freeTierPctLeft(active({ spend: 12, remaining: 0 }))).toBe(0);
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

// ── freeTierCreditLeft (2026-07-28: the row value is dollars, not tokens) ──

test('freeTierCreditLeft reports the wallet in its own unit', () => {
  const quota = {
    enabled: true, status: 'active',
    currency: 'USD', max_budget: 10, spend: 3.8, remaining: 6.2,
  } as never
  expect(freeTierCreditLeft(quota)).toEqual({
    remaining: 6.2, total: 10, currency: 'USD',
  })
})

test('freeTierCreditLeft floors a negative remaining at zero', () => {
  const quota = {
    enabled: true, status: 'exhausted',
    currency: 'USD', max_budget: 10, spend: 11, remaining: -1,
  } as never
  expect(freeTierCreditLeft(quota)).toEqual({
    remaining: 0, total: 10, currency: 'USD',
  })
})

test('freeTierCreditLeft null exactly when there is no bar to annotate', () => {
  expect(freeTierCreditLeft(null)).toBeNull()
  expect(freeTierCreditLeft({ enabled: false } as never)).toBeNull()
  expect(freeTierCreditLeft({ enabled: true, status: 'uninitialized' } as never)).toBeNull()
})

describe('creditMoney (free-tier wallet)', () => {
  it('keeps six decimals so sub-cent usage is visible', () => {
    // The reason this formatter exists: a real turn on the free tier costs
    // fractions of a cent. At two decimals $9.993714 renders as "9.99" and a
    // day of use looks like nothing happened at all.
    expect(creditMoney(9.993714)).toBe('9.993714');
    expect(creditMoney(9.999031)).toBe('9.999031');
  });

  it('pads so the digit count never jumps between renders', () => {
    expect(creditMoney(10)).toBe('10.000000');
  });

  it('shows a dash for missing values, like money()', () => {
    expect(creditMoney(null)).toBe('—');
    expect(creditMoney(undefined)).toBe('—');
    expect(creditMoney('')).toBe('—');
  });

  it('leaves the two-decimal money() alone — balances are not sub-cent', () => {
    expect(money(9.993714)).toBe('9.99');
  });
});
