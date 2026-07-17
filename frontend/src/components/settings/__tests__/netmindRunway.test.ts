/**
 * @file netmindRunway.test.ts
 * @description Unit tests for deriveRunway — the pure classifier that decides
 * whether the account panel is in a calm ("healthy") state or should promote an
 * action ("low"). Drives the plan × runway state machine (upsell only on low).
 */

import { describe, it, expect } from 'vitest';
import { deriveRunway, LOW_BALANCE_USD } from '../netmindRunway';
import type { QuotaMeResponse, FeeInfo } from '@/types';

// Minimal typed factories — deriveRunway only reads `enabled`/`status` off quota
// and `eligible`/`checks`/`metrics.free_credit` off fee.
const quotaActive = { enabled: true, status: 'active' } as unknown as QuotaMeResponse;
const quotaExhausted = { enabled: true, status: 'exhausted' } as unknown as QuotaMeResponse;
const quotaUninit = { enabled: true, status: 'uninitialized' } as QuotaMeResponse;
const quotaOff = { enabled: false } as QuotaMeResponse;

const fee = (over: Partial<FeeInfo> & { balance?: string }): FeeInfo => ({
  eligible: over.eligible,
  checks: over.checks,
  metrics: over.balance !== undefined ? { free_credit: over.balance } : over.metrics,
});

describe('deriveRunway', () => {
  it('free tier active → healthy regardless of balance', () => {
    expect(deriveRunway(quotaActive, fee({ balance: '0' }))).toBe('healthy');
    expect(deriveRunway(quotaActive, null)).toBe('healthy');
  });

  it('free tier active → healthy even with arrears/ineligible (warnings shown separately)', () => {
    expect(deriveRunway(quotaActive, fee({ checks: { has_arrears: true } }))).toBe('healthy');
    expect(deriveRunway(quotaActive, fee({ eligible: false }))).toBe('healthy');
  });

  it('free tier exhausted → healthy only when balance covers the buffer', () => {
    expect(deriveRunway(quotaExhausted, fee({ balance: '5.00' }))).toBe('healthy');
    expect(deriveRunway(quotaExhausted, fee({ balance: '0.40' }))).toBe('low');
    expect(deriveRunway(quotaExhausted, fee({ balance: '0' }))).toBe('low');
    expect(deriveRunway(quotaExhausted, null)).toBe('low');
  });

  it('exhausted free tier + arrears/ineligible → low', () => {
    expect(deriveRunway(quotaExhausted, fee({ balance: '99', checks: { has_arrears: true } }))).toBe('low');
    expect(deriveRunway(quotaExhausted, fee({ balance: '99', eligible: false }))).toBe('low');
  });

  it('quota feature off → falls back to balance buffer', () => {
    expect(deriveRunway(quotaOff, fee({ balance: '5.00' }))).toBe('healthy');
    expect(deriveRunway(quotaOff, fee({ balance: '0.10' }))).toBe('low');
  });

  it('quota uninitialized → treated as no free tier (balance decides)', () => {
    expect(deriveRunway(quotaUninit, fee({ balance: '5.00' }))).toBe('healthy');
    expect(deriveRunway(quotaUninit, fee({ balance: '0' }))).toBe('low');
  });

  it('boundary: balance exactly at threshold is healthy', () => {
    expect(deriveRunway(quotaExhausted, fee({ balance: String(LOW_BALANCE_USD) }))).toBe('healthy');
  });

  it('malformed balance string → low (never crash, never over-credit)', () => {
    expect(deriveRunway(quotaExhausted, fee({ balance: 'abc' }))).toBe('low');
    expect(deriveRunway(null, null)).toBe('low');
  });
});
