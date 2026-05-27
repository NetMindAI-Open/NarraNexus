/**
 * @file_name: wsAuthError.ts
 * @description: Detect WebSocket auth-error frames so the global
 * `narranexus:auth-expired` event fires symmetrically with the REST path.
 *
 * Background: backend/routes/websocket.py sends seven distinct AuthError
 * frames (L426-499) when JWT validation fails — all carry
 * `error_type: 'AuthError'` and one of the canonical messages
 * 'Token expired' / 'Invalid token' / 'Authentication required'.
 * Pre-fix, wsManager just rendered these as red chat bubbles; user
 * had no way to know their session expired and no path to re-login.
 *
 * Helper extracted from wsManager so both `run()` and `reconnect()`
 * onmessage handlers can share it AND so the logic is unit-testable
 * without spinning up a real WebSocket.
 */

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
 * Fire the app-wide `narranexus:auth-expired` event. App.tsx listens
 * for it and calls configStore.logout(); a banner explains why.
 *
 * Idempotent at the listener level — App's handler bails when
 * `isLoggedIn` is already false.
 */
export function dispatchAuthExpired(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent('narranexus:auth-expired'));
}
