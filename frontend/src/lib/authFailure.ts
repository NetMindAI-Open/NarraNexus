/**
 * @file_name: authFailure.ts
 * @description: Tell "your session is dead" apart from every other 401.
 *
 * The backend tags every 401 with a machine-readable `code`
 * (backend/auth_errors.py). Only a small, explicit set of codes means the
 * session itself is gone; everything else is a local failure the calling
 * screen should handle on its own.
 *
 * Why an allowlist and not a denylist: the old code had a denylist — any
 * 401 logged you out *unless* the endpoint was `/api/auth/login` or under
 * `/api/billing/`. Every endpoint someone forgot to add was a live grenade,
 * and `/api/providers`'s NetMind-token 401 was one of them (2026-08-02: it
 * bounced demo users to /login while their session was perfectly valid).
 * With an allowlist, a code nobody has classified yet fails *safe*.
 */

/**
 * Mirrors `SESSION_DEAD_CODES` in backend/auth_errors.py. Keep the two in
 * sync — a code that exists on only one side simply never matches, which
 * (deliberately) degrades to "don't log the user out".
 */
export const SESSION_DEAD_CODES: ReadonlySet<string> = new Set([
  'token_expired',   // JWT past its exp
  'token_invalid',   // signature/claims rejected
  'token_missing',   // no Bearer reached the backend
  'identity_missing', // local mode: frontend has no X-User-Id to send
]);

/** Pull the `code` out of a parsed 401 body; null when absent or malformed. */
export function readAuthCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const code = (body as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : null;
}

/** True only for the codes that mean the session itself is gone. */
export function isSessionDeadFailure(body: unknown): boolean {
  const code = readAuthCode(body);
  return code !== null && SESSION_DEAD_CODES.has(code);
}
