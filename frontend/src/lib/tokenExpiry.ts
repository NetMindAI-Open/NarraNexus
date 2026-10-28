/**
 * @file_name: tokenExpiry.ts
 * @description: Read the session JWT's own expiry, client-side.
 *
 * The backend issues a 7-day JWT (backend/auth.py `JWT_EXPIRY_DAYS`) and
 * offers no refresh endpoint: when it expires there is no renewal path,
 * only a re-login. Until now the frontend never looked at `exp`, so the
 * first sign of expiry was a 401 on whatever the user clicked next — the
 * session ended at a moment chosen by the clock, mid-run, with unsaved
 * state on screen.
 *
 * Reading `exp` lets the UI say so in advance. The claims are decoded
 * WITHOUT verification, which is fine because nothing here is an
 * authorization decision: the backend verifies every request, and the
 * worst a tampered `exp` can do is show its own holder a wrong banner.
 */

/** How far ahead of expiry to start warning. */
export const EXPIRY_WARNING_WINDOW_MS = 24 * 60 * 60 * 1000;

/** How often the app shell re-checks. One base64 decode — cost is nil. */
export const EXPIRY_CHECK_INTERVAL_MS = 10 * 60 * 1000;

/** Below this, a dismissed warning comes back one final time. */
export const FINAL_REMINDER_MS = 60 * 60 * 1000;

/**
 * Should the warning banner be on screen?
 *
 * `dismissedAt` is the remaining-time value at the moment the user
 * dismissed (null = never dismissed). One re-arm: dismissing "expires in
 * 23 hours" must not buy silence all the way to zero, because by then the
 * warning is the only thing standing between the user and losing what is
 * on screen. Dismissing inside the final hour is respected for good.
 */
export function shouldShowExpiryWarning(
  msLeft: number | null,
  dismissedAt: number | null,
): boolean {
  if (msLeft === null || msLeft > EXPIRY_WARNING_WINDOW_MS) return false;
  if (dismissedAt === null) return true;
  return msLeft <= FINAL_REMINDER_MS && dismissedAt > FINAL_REMINDER_MS;
}

function decodeSegment(segment: string): unknown {
  // base64url → base64, then pad to a multiple of 4.
  const b64 = segment.replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  return JSON.parse(atob(padded));
}

/** Epoch **seconds** at which the token dies, or null if unreadable. */
export function readTokenExp(token: string): number | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const claims = decodeSegment(parts[1]);
    if (!claims || typeof claims !== 'object') return null;
    const exp = (claims as { exp?: unknown }).exp;
    return typeof exp === 'number' && Number.isFinite(exp) ? exp : null;
  } catch {
    // Malformed base64 / JSON. This runs on the app-shell render path,
    // so it must degrade to "expiry unknown", never throw.
    return null;
  }
}

/**
 * Milliseconds of session left; 0 once expired, null when there is
 * nothing to expire (local mode sends no JWT) or the token is unreadable.
 */
export function msUntilExpiry(token: string, now: number = Date.now()): number | null {
  const exp = readTokenExp(token);
  if (exp === null) return null;
  return Math.max(0, exp * 1000 - now);
}

/**
 * Human phrasing for the warning banner. Coarse on purpose — a countdown
 * ticking down to zero reads as a threat; "in about 6 hours" reads as
 * information the user can act on whenever suits them.
 */
export function formatExpiryDistance(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return 'less than a minute';
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'}`;
  const hours = Math.round(minutes / 60);
  return `${hours} hour${hours === 1 ? '' : 's'}`;
}
