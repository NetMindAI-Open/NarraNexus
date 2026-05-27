/**
 * @file_name: useArtifactHeal.test.tsx
 * @description: Behavior contract for the self-heal modal state machine.
 *
 * Locks the dismiss-loop fix: once the user has explicitly dismissed the
 * "no matching file" modal for an artifact, subsequent attempt() calls for
 * the same artifact must NOT re-open the modal. Pre-fix, the renderer's
 * useEffect would fire again after every dismiss (because the returned
 * object literal was a fresh reference each render), HEAD-410 again, call
 * attempt() again, and re-open the modal — making it impossible to close.
 *
 * Symptom this guards against: P0 bug "Artifact 在 workspace 手动删除后，
 * 主页面显示弹窗无法关闭导致无法使用应用" (2026-05-25, Jiaxi Chen).
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const healMock = vi.fn();
vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    heal: (...args: unknown[]) => healMock(...args),
  },
}));

// Real zustand returns stable refs for store fields across renders. The
// mock has to do the same — a fresh `vi.fn()` per call would defeat the
// hook's useMemo by making `upsert` change identity every render.
const stableUpsert = vi.fn();
const stableStore = { upsert: stableUpsert };
vi.mock('@/stores', () => ({
  useArtifactStore: (selector: (s: { upsert: () => void }) => unknown) =>
    selector(stableStore),
}));

import { useArtifactHeal } from '../useArtifactHeal';

beforeEach(() => {
  healMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useArtifactHeal', () => {
  test('attempt() opens the modal when server returns no candidates', async () => {
    healMock.mockResolvedValue({
      recovered: false,
      artifact: null,
      candidates: [],
      message: 'no matching file in workspace',
    });

    const { result } = renderHook(() => useArtifactHeal('agent_x', 'art_y'));
    expect(result.current.modalOpen).toBe(false);

    await act(async () => {
      await result.current.attempt();
    });

    expect(result.current.modalOpen).toBe(true);
    expect(result.current.message).toBe('no matching file in workspace');
  });

  test('dismiss() closes the modal and a SUBSEQUENT attempt() does NOT re-open it', async () => {
    // This is the bug fix: after dismiss, the artifact is considered
    // user-dismissed for the lifetime of this hook instance. Re-attempts
    // (triggered by renderer useEffects firing again) must stay silent so
    // the user is not trapped in an infinite modal loop.
    healMock.mockResolvedValue({
      recovered: false,
      artifact: null,
      candidates: [],
      message: 'no matching file in workspace',
    });

    const { result } = renderHook(() => useArtifactHeal('agent_x', 'art_y'));

    await act(async () => {
      await result.current.attempt();
    });
    expect(result.current.modalOpen).toBe(true);

    act(() => {
      result.current.dismiss();
    });
    expect(result.current.modalOpen).toBe(false);

    // Simulate the renderer's useEffect firing again (HEAD 410 still
    // returns the same broken pointer) and calling attempt() once more.
    await act(async () => {
      await result.current.attempt();
    });

    expect(result.current.modalOpen).toBe(false);
  });

  test('dismiss state is scoped per artifact — switching artifactId resets it', async () => {
    healMock.mockResolvedValue({
      recovered: false,
      artifact: null,
      candidates: [],
      message: 'no matching file in workspace',
    });

    const { result, rerender } = renderHook(
      ({ aid }: { aid: string }) => useArtifactHeal('agent_x', aid),
      { initialProps: { aid: 'art_1' } },
    );

    await act(async () => {
      await result.current.attempt();
    });
    act(() => {
      result.current.dismiss();
    });
    expect(result.current.modalOpen).toBe(false);

    rerender({ aid: 'art_2' });

    await act(async () => {
      await result.current.attempt();
    });
    expect(result.current.modalOpen).toBe(true);
  });

  test('successful recovery (recovered=true) bumps recoveryVersion and closes modal', async () => {
    healMock
      .mockResolvedValueOnce({
        recovered: false,
        artifact: null,
        candidates: [],
        message: 'first attempt no match',
      })
      .mockResolvedValueOnce({
        recovered: true,
        artifact: {
          artifact_id: 'art_y',
          agent_id: 'agent_x',
          file_path: 'agent_x_user_z/report/index.html',
          size_bytes: 100,
        },
        candidates: [],
        message: 'recovered onto picked path',
      });

    const { result } = renderHook(() => useArtifactHeal('agent_x', 'art_y'));

    await act(async () => {
      await result.current.attempt();
    });
    expect(result.current.modalOpen).toBe(true);
    const versionBefore = result.current.recoveryVersion;

    await act(async () => {
      await result.current.attempt('agent_x_user_z/report/index.html');
    });
    expect(result.current.modalOpen).toBe(false);
    expect(result.current.recoveryVersion).toBe(versionBefore + 1);
  });

  test('returned object identity is stable across renders (no deps churn)', () => {
    healMock.mockResolvedValue({
      recovered: false,
      artifact: null,
      candidates: [],
      message: '',
    });

    const { result, rerender } = renderHook(() =>
      useArtifactHeal('agent_x', 'art_y'),
    );

    const first = result.current;
    rerender();
    const second = result.current;

    // The hook's return value must be a memoized object so consumer
    // useEffect deps like [url, heal] don't churn on every render.
    expect(second).toBe(first);
  });
});
