/**
 * @file_name: useFlashFlag.test.ts
 * @description: The self-clearing "Saved" flag of the model editors.
 *
 * Locks: raise -> on, off after the duration; a second raise restarts the
 * countdown (the first timer must not clear the newer confirmation early);
 * unmount clears the pending timer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useFlashFlag } from '../useFlashFlag';

describe('useFlashFlag', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('is off until raised and turns off after the duration', () => {
    const { result } = renderHook(() => useFlashFlag(2500));
    expect(result.current[0]).toBe(false);

    act(() => result.current[1]());
    expect(result.current[0]).toBe(true);

    act(() => { vi.advanceTimersByTime(2499); });
    expect(result.current[0]).toBe(true);
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current[0]).toBe(false);
  });

  it('a second raise restarts the countdown', () => {
    const { result } = renderHook(() => useFlashFlag(2500));
    act(() => result.current[1]());
    act(() => { vi.advanceTimersByTime(2000); });
    act(() => result.current[1]());

    // The first raise's deadline passes: the newer confirmation stays.
    act(() => { vi.advanceTimersByTime(1000); });
    expect(result.current[0]).toBe(true);

    act(() => { vi.advanceTimersByTime(1500); });
    expect(result.current[0]).toBe(false);
  });

  it('clears the pending timer on unmount', () => {
    const { result, unmount } = renderHook(() => useFlashFlag(2500));
    act(() => result.current[1]());
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
