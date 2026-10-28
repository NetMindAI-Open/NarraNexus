/**
 * @file_name: wsAuthError.ts
 * @description: Detect WebSocket auth-error frames and hand them to the
 * same session guard the REST path uses, so both channels reach the same
 * verdict about whether the session is actually dead.
 *
 * Background: backend/routes/websocket.py sends seven distinct AuthError
 * frames when auth fails — all carry `error_type: 'AuthError'`, one of the
 * canonical messages ('Token expired' / 'Invalid token' /
 * 'Authentication required'), and since 2026-08-06 an `error_code`.
 * Before any of this existed, wsManager just rendered them as red chat
 * bubbles; the user had no way to know their session expired and no path
 * to re-login. The fix for THAT then over-corrected into logging the user
 * out on any AuthError frame at all — including frames that describe a
 * frontend state bug rather than a dead session.
 *
 * Helper extracted from wsManager so both `run()` and `reconnect()`
 * onmessage handlers can share it AND so the logic is unit-testable
 * without spinning up a real WebSocket.
 */

import { confirmSessionDeath } from '@/lib/sessionGuard';

export interface MaybeAuthErrorFrame {
  type?: unknown;
  error_type?: unknown;
  error_message?: unknown;
  [key: string]: unknown;
}

const AUTH_MESSAGE_SUBSTRINGS = [
  'token expired',
  'invalid token',
  'authentication required',
];

/**
 * True iff `message` looks like one of the backend's AuthError frames.
 *
 * Primary signal: `error_type === 'AuthError'` (set on every frame
 * websocket.py:426-499). Fallback: substring match on `error_message`
 * for any future code path that forgets to set `error_type`.
 */
export function isAuthErrorMessage(message: unknown): boolean {
  if (!message || typeof message !== 'object') return false;
  const m = message as MaybeAuthErrorFrame;
  if (m.type !== 'error') return false;
  if (m.error_type === 'AuthError') return true;
  if (typeof m.error_message !== 'string') return false;
  const lower = m.error_message.toLowerCase();
  return AUTH_MESSAGE_SUBSTRINGS.some((s) => lower.includes(s));
}

/**
 * Route a WS auth-rejection through the same confirmation the REST path
 * uses: probe `GET /api/auth/session`, and only tear the session down if
 * the probe agrees it is dead.
 *
 * Why not dispatch `narranexus:auth-expired` straight from here (as this
 * function used to): not every AuthError frame is about the session. The
 * local-mode frames ("user_id mismatch between URL and payload") describe
 * a frontend state bug, and a run-scoped rejection says nothing about
 * whether the JWT is still good. Letting the probe adjudicate means a
 * misclassified frame costs a failed run, not the user's whole session.
 */
export function reportWsAuthFailure(message: unknown): void {
  if (typeof window === 'undefined') return;
  const code =
    message && typeof message === 'object'
      ? (message as { error_code?: unknown }).error_code
      : undefined;
  void confirmSessionDeath({
    endpoint: 'ws',
    code: typeof code === 'string' ? code : null,
  });
}
