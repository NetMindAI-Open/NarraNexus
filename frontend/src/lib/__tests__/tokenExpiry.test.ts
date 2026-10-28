/**
 * @file_name: tokenExpiry.test.ts
 * @description: The session JWT lasts 7 days (backend/auth.py
 * JWT_EXPIRY_DAYS) and there is no refresh flow — when it dies, it dies
 * mid-click, and the user lands on /login with no warning. Reading `exp`
 * client-side is what lets the UI warn while the session is still usable,
 * so the user picks the moment to re-login instead of the JWT picking it
 * for them (2026-08-02: several users were picked for, mid-demo).
 *
 * Parsing is deliberately unverified — signature checking is the
 * backend's job. A forged `exp` can only mislead the user's own banner.
 */
import { describe, expect, test } from 'vitest';
import {
  EXPIRY_WARNING_WINDOW_MS,
  FINAL_REMINDER_MS,
  formatExpiryDistance,
  msUntilExpiry,
  readTokenExp,
  shouldShowExpiryWarning,
} from '../tokenExpiry';

const HOUR = 60 * 60 * 1000;

/** Minimal unsigned JWT with the given payload — enough for exp reading. */
function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.signature`;
}

describe('readTokenExp', () => {
  test('reads exp from a well-formed token', () => {
    expect(readTokenExp(fakeJwt({ user_id: 'alice', exp: 1_800_000_000 }))).toBe(1_800_000_000);
  });

  test('base64url payloads (- and _) decode correctly', () => {
    const token = fakeJwt({ user_id: 'a?b>c?', exp: 1_700_000_000 });
    expect(readTokenExp(token)).toBe(1_700_000_000);
  });

  test('returns null for junk rather than throwing', () => {
    // Never let a malformed token take down the app shell — this runs on
    // every render path that shows the banner.
    expect(readTokenExp('')).toBeNull();
    expect(readTokenExp('not-a-jwt')).toBeNull();
    expect(readTokenExp('a.b')).toBeNull();
    expect(readTokenExp('a.!!!.c')).toBeNull();
    expect(readTokenExp(fakeJwt({ user_id: 'alice' }))).toBeNull();
    expect(readTokenExp(fakeJwt({ exp: 'soon' }))).toBeNull();
  });
});

describe('msUntilExpiry', () => {
  const now = 1_700_000_000_000;

  test('reports the remaining lifetime', () => {
    const token = fakeJwt({ exp: now / 1000 + 3 * 3600 });
    expect(msUntilExpiry(token, now)).toBe(3 * HOUR);
  });

  test('an already-expired token reports 0, never a negative', () => {
    const token = fakeJwt({ exp: now / 1000 - 3600 });
    expect(msUntilExpiry(token, now)).toBe(0);
  });

  test('no token / no exp → null (local mode has nothing to expire)', () => {
    expect(msUntilExpiry('', now)).toBeNull();
    expect(msUntilExpiry(fakeJwt({ user_id: 'alice' }), now)).toBeNull();
  });

  test('distance is phrased coarsely, with correct pluralisation', () => {
    expect(formatExpiryDistance(6 * HOUR)).toBe('6 hours');
    expect(formatExpiryDistance(HOUR)).toBe('1 hour');
    expect(formatExpiryDistance(45 * 60 * 1000)).toBe('45 minutes');
    expect(formatExpiryDistance(60 * 1000)).toBe('1 minute');
    expect(formatExpiryDistance(0)).toBe('less than a minute');
  });

  test('the warning window is wide enough to act on', () => {
    // Placed here so the constant's intent is pinned next to its use.
    // A user who sees "expires in 20 minutes" mid-run cannot do much. A day
    // of notice lets them re-login at a moment of their choosing.
    expect(EXPIRY_WARNING_WINDOW_MS).toBe(24 * HOUR);
  });
});

describe('shouldShowExpiryWarning', () => {
  test('silent while the session has plenty of life left', () => {
    expect(shouldShowExpiryWarning(3 * 24 * HOUR, null)).toBe(false);
  });

  test('shows once inside the warning window', () => {
    expect(shouldShowExpiryWarning(6 * HOUR, null)).toBe(true);
  });

  test('local mode (nothing to expire) never warns', () => {
    expect(shouldShowExpiryWarning(null, null)).toBe(false);
  });

  test('dismissal is respected — for a while', () => {
    expect(shouldShowExpiryWarning(20 * HOUR, 23 * HOUR)).toBe(false);
  });

  test('but comes back once for the final hour', () => {
    // Dismissing "expires in 23 hours" must not buy silence all the way to
    // zero — by then the warning is the only thing between the user and
    // losing what is on screen.
    expect(shouldShowExpiryWarning(30 * 60 * 1000, 23 * HOUR)).toBe(true);
  });

  test('dismissing inside the final hour stays dismissed', () => {
    expect(shouldShowExpiryWarning(10 * 60 * 1000, 30 * 60 * 1000)).toBe(false);
  });

  test('the final-reminder threshold is one hour', () => {
    expect(FINAL_REMINDER_MS).toBe(HOUR);
  });
});
