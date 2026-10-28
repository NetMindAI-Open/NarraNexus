/**
 * @file_name: useFastMode.test.ts
 * @description: Per-agent fast-mode preference — localStorage-backed.
 *
 * Locks: default off; setting persists per agent (agent A on must not turn
 * agent B on); corrupted storage degrades to off instead of throwing.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFastMode } from '../useFastMode';

describe('useFastMode', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to false and persists per agent', () => {
    const { result } = renderHook(() => useFastMode('agent-1'));
    expect(result.current[0]).toBe(false);

    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);

    const again = renderHook(() => useFastMode('agent-1'));
    expect(again.result.current[0]).toBe(true);

    const other = renderHook(() => useFastMode('agent-2'));
    expect(other.result.current[0]).toBe(false);
  });

  it('turning off removes the persisted entry', () => {
    const { result } = renderHook(() => useFastMode('agent-1'));
    act(() => result.current[1](true));
    act(() => result.current[1](false));
    expect(localStorage.getItem('narra-nexus-fast-mode')).toBe('{}');
  });

  it('re-reads storage when the agent changes on one hook instance', () => {
    // ChatPanel holds a single hook instance across agent switches — the
    // render-time adjustment is the branch that prevents agent A's fast
    // setting from bleeding onto agent B.
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useFastMode(id),
      { initialProps: { id: 'agent-1' } },
    );
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);

    rerender({ id: 'agent-2' });
    expect(result.current[0]).toBe(false);

    rerender({ id: 'agent-1' });
    expect(result.current[0]).toBe(true);
  });

  it('survives corrupted storage', () => {
    localStorage.setItem('narra-nexus-fast-mode', '{not json');
    const { result } = renderHook(() => useFastMode('agent-1'));
    expect(result.current[0]).toBe(false);
  });

  it('treats a scalar payload as empty', () => {
    localStorage.setItem('narra-nexus-fast-mode', '5');
    const { result } = renderHook(() => useFastMode('agent-1'));
    expect(result.current[0]).toBe(false);
  });

  it('treats a non-object payload (array) as empty', () => {
    localStorage.setItem('narra-nexus-fast-mode', '["agent-1"]');
    const { result } = renderHook(() => useFastMode('agent-1'));
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(JSON.parse(localStorage.getItem('narra-nexus-fast-mode') ?? '')).toEqual({
      'agent-1': true,
    });
  });

  it('is inert without an agent id', () => {
    const { result } = renderHook(() => useFastMode(undefined));
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(false);
    expect(localStorage.getItem('narra-nexus-fast-mode')).toBeNull();
  });
});
