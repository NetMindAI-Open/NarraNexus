/**
 * @file_name: wsCircuitOpen.test.ts
 * @description: Unit tests for the WS circuit-open frame detector + dispatch.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import {
  circuitOpenReason,
  dispatchAgentCircuitOpen,
  isCircuitOpenMessage,
  shouldClearCircuitBanner,
  syncCircuitBannerReason,
} from '../wsCircuitOpen';

describe('isCircuitOpenMessage', () => {
  it('matches the backend circuit-open frame', () => {
    expect(
      isCircuitOpenMessage({ type: 'error', error_type: 'agent_circuit_open', cb_reason: 'paused:auth' })
    ).toBe(true);
  });

  it('rejects other error frames', () => {
    expect(isCircuitOpenMessage({ type: 'error', error_type: 'AuthError' })).toBe(false);
    expect(isCircuitOpenMessage({ type: 'error', error_type: 'NotFound' })).toBe(false);
  });

  it('rejects non-error / malformed input', () => {
    expect(isCircuitOpenMessage({ type: 'text_delta' })).toBe(false);
    expect(isCircuitOpenMessage(null)).toBe(false);
    expect(isCircuitOpenMessage('nope')).toBe(false);
  });
});

describe('circuitOpenReason', () => {
  it('extracts cb_reason for a matching frame', () => {
    expect(
      circuitOpenReason({ type: 'error', error_type: 'agent_circuit_open', cb_reason: 'paused:quota' })
    ).toBe('paused:quota');
  });

  it('returns "" for a non-matching frame', () => {
    expect(circuitOpenReason({ type: 'error', error_type: 'AuthError' })).toBe('');
  });
});

describe('shouldClearCircuitBanner', () => {
  it('keeps the banner while the backend still reports paused', () => {
    expect(shouldClearCircuitBanner('paused')).toBe(false);
  });

  it('clears the banner once the breaker is active again (self-heal)', () => {
    expect(shouldClearCircuitBanner('active')).toBe(true);
  });

  it('clears the banner for cooling too (no longer paused)', () => {
    expect(shouldClearCircuitBanner('cooling')).toBe(true);
  });

  it('keeps the banner while a half-open probe is still in flight', () => {
    // The verdict is unknown and the next message would still be refused;
    // closing now only to re-open on a failed probe would flap.
    expect(shouldClearCircuitBanner('probing')).toBe(false);
  });
});

describe('syncCircuitBannerReason', () => {
  it('closes the banner once the breaker is active or merely cooling', () => {
    expect(syncCircuitBannerReason('probing', 'active', null)).toBeNull();
    expect(syncCircuitBannerReason('paused:auth', 'cooling', null)).toBeNull();
  });

  it('escalates a probing banner to the real pause when the probe failed', () => {
    expect(syncCircuitBannerReason('probing', 'paused', 'auth')).toBe('paused:auth');
    expect(syncCircuitBannerReason('probing', 'paused', null)).toBe('paused:unknown');
  });

  it('keeps the current reason while a probe is in flight or the status is unknown', () => {
    expect(syncCircuitBannerReason('probing', 'probing', null)).toBe('probing');
    expect(syncCircuitBannerReason('paused:quota', 'probing', null)).toBe('paused:quota');
    expect(syncCircuitBannerReason('probing', 'something_new', null)).toBe('probing');
  });
});

describe('dispatchAgentCircuitOpen', () => {
  afterEach(() => vi.restoreAllMocks());

  it('fires a narranexus:agent-circuit-open CustomEvent with detail', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    dispatchAgentCircuitOpen({ agentId: 'ag_1', reason: 'paused:auth' });
    expect(spy).toHaveBeenCalledTimes(1);
    const evt = spy.mock.calls[0][0] as CustomEvent;
    expect(evt.type).toBe('narranexus:agent-circuit-open');
    expect(evt.detail).toEqual({ agentId: 'ag_1', reason: 'paused:auth' });
  });
});
