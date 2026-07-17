/**
 * @file_name: wsCircuitOpen.test.ts
 * @description: Unit tests for the WS circuit-open frame detector + dispatch.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import {
  circuitOpenReason,
  dispatchAgentCircuitOpen,
  isCircuitOpenMessage,
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
