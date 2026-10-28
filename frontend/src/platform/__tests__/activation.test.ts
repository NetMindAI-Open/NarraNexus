/**
 * @file_name: activation.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Frontend activation fires once per plugin, isolates failures into the error sink, and allows explicit retry.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { activateNow, activationState, fireActivation, registerActivation, resetActivation } from '@/platform/activation';
import { onUiError, recentUiErrors, resetErrorSink } from '@/platform/errorSink';

afterEach(() => {
  resetActivation();
  resetErrorSink();
});

describe('activation', () => {
  it('fires once and records state', async () => {
    let calls = 0;
    registerActivation('acme.a', ['onPage:acme.a.home', 'onStartup'], async () => {
      calls += 1;
    });
    expect(activationState('acme.a').state).toBe('pending');
    expect(await fireActivation('onPage:acme.a.home')).toEqual(['acme.a']);
    expect(await fireActivation('onStartup')).toEqual([]);
    expect(calls).toBe(1);
    expect(activationState('acme.a').state).toBe('active');
    expect(activationState('nope').state).toBe('unknown');
  });

  it('failure is isolated, reported and retryable', async () => {
    const seen: string[] = [];
    onUiError((r) => seen.push(r.source));
    let attempts = 0;
    registerActivation('acme.bad', ['onStartup'], async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('boom');
    });
    await fireActivation('onStartup');
    expect(activationState('acme.bad')).toEqual({ state: 'failed', error: 'boom' });
    expect(seen).toEqual(['acme.bad']);
    expect(recentUiErrors()[0].kind).toBe('chunk');
    expect(await fireActivation('onStartup')).toEqual([]); // not retried by events
    await activateNow('acme.bad');
    expect(activationState('acme.bad').state).toBe('active');
    await expect(activateNow('nope')).rejects.toThrow(/not registered/);
  });
});
