/**
 * @file_name: wsManager.authError.test.ts
 * @description: Pin the AuthError → narranexus:auth-expired bridge.
 *
 * Symptom this guards against (TODO 2026-05-26): the REST path dispatched
 * narranexus:auth-expired when a 401 came back, but the WS path didn't —
 * so a stale-JWT cloud user got "Token expired" red bubbles in chat with
 * no logout / no redirect. The chat surface was the only signal something
 * was wrong, leaving the user stuck.
 *
 * Backend WS sends seven AuthError frames (websocket.py L426-499) all
 * carrying `error_type: 'AuthError'`. The bridge keys on that field
 * primarily and the canonical error_message strings as a fallback.
 */
import { describe, expect, test } from 'vitest';
import { isAuthErrorMessage } from '../wsAuthError';

describe('isAuthErrorMessage', () => {
  test('returns true for canonical AuthError frame', () => {
    expect(
      isAuthErrorMessage({ type: 'error', error_type: 'AuthError', error_message: 'Token expired' }),
    ).toBe(true);
  });

  test('returns true for any error_type=AuthError, regardless of message', () => {
    expect(
      isAuthErrorMessage({ type: 'error', error_type: 'AuthError', error_message: '' }),
    ).toBe(true);
  });

  test('falls back to error_message match when error_type missing', () => {
    // Some legacy paths produce error frames without error_type. Match the
    // three canonical strings from backend/auth.py and websocket.py.
    expect(
      isAuthErrorMessage({ type: 'error', error_message: 'Token expired' }),
    ).toBe(true);
    expect(
      isAuthErrorMessage({ type: 'error', error_message: 'Invalid token' }),
    ).toBe(true);
    expect(
      isAuthErrorMessage({ type: 'error', error_message: 'Authentication required' }),
    ).toBe(true);
  });

  test('error_message match is case-insensitive', () => {
    expect(
      isAuthErrorMessage({ type: 'error', error_message: 'TOKEN EXPIRED' }),
    ).toBe(true);
  });

  test('returns false for non-error frames', () => {
    expect(isAuthErrorMessage({ type: 'agent_thinking', content: 'hi' })).toBe(false);
    expect(isAuthErrorMessage({ type: 'complete' })).toBe(false);
    expect(isAuthErrorMessage({ type: 'heartbeat' })).toBe(false);
  });

  test('returns false for non-auth error frames', () => {
    expect(
      isAuthErrorMessage({
        type: 'error',
        error_type: 'AgentError',
        error_message: 'Tool execution failed',
      }),
    ).toBe(false);
    expect(
      isAuthErrorMessage({ type: 'error', error_message: 'Rate limited' }),
    ).toBe(false);
  });

  test('handles null/undefined/non-object inputs without throwing', () => {
    expect(isAuthErrorMessage(null)).toBe(false);
    expect(isAuthErrorMessage(undefined)).toBe(false);
    expect(isAuthErrorMessage('not an object' as unknown as object)).toBe(false);
    expect(isAuthErrorMessage(42 as unknown as object)).toBe(false);
  });
});
