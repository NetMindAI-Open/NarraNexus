/**
 * @file_name: ChartRenderer.test.tsx
 * @description: Lifecycle contract for the 0802 fixes — the deferred-init /
 * resize / registry logic lives in the component, so the store tests alone
 * cannot prove it (review #290-④).
 *
 * A ResizeObserver stub captures the callback + observed node so the test
 * can drive box-size transitions by hand and assert:
 *   - a 0×0 container (the LRU pool's display:none pane) never calls
 *     echarts.init — the bug that left a permanently blank canvas;
 *   - init runs exactly once when the container gains area, and registers;
 *   - a later size change calls chart.resize(), not a second init;
 *   - unmount unregisters by identity (the same instance it registered).
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

let boxWidth = 0;
let boxHeight = 0;
let roCallback: (() => void) | null = null;
let roDisconnected = false;

class ResizeObserverStub {
  // Model real RO: once disconnect() runs, the callback stops firing — so
  // the "disconnect stops re-throwing" assertion is meaningful when the
  // test drives the callback by hand.
  constructor(cb: () => void) {
    roCallback = () => {
      if (!roDisconnected) cb();
    };
  }
  observe() {}
  disconnect() {
    roDisconnected = true;
  }
}

const chartInstance = {
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  getDataURL: vi.fn(() => ''),
};
const initMock = vi.fn(() => chartInstance);

vi.mock('echarts', () => ({ init: (...a: unknown[]) => initMock(...a) }));
vi.mock('@/services/artifactsApi', () => ({
  fetchArtifactText: vi.fn(async () => JSON.stringify({ series: [] })),
}));
vi.mock('@/hooks/useArtifactRawUrl', () => ({
  useArtifactRawUrl: () => ({ url: '/raw/FAKE/', error: null, reload: vi.fn() }),
}));
vi.mock('@/hooks/useArtifactHeal', () => ({
  useArtifactHeal: () => ({
    attempt: vi.fn(),
    recoveryVersion: 0,
    modalOpen: false,
    candidates: [],
    message: '',
    busy: false,
    dismiss: vi.fn(),
  }),
}));
vi.mock('@/lib/echarts-nm-theme', () => ({ pickNMTheme: () => 'nm-light' }));

const registerMock = vi.fn();
const unregisterMock = vi.fn();
vi.mock('@/stores/artifactStore', () => ({
  useArtifactStore: (selector: (s: unknown) => unknown) =>
    selector({ registerChartInstance: registerMock, unregisterChartInstance: unregisterMock }),
}));

import ChartRenderer from '../ChartRenderer';
import type { Artifact } from '@/types/artifact';

const artifact = {
  artifact_id: 'c1',
  agent_id: 'agent_x',
  kind: 'application/vnd.echarts+json',
  title: 'Chart',
  updated_at: '2026-08-12T00:00:00Z',
} as unknown as Artifact;

beforeEach(() => {
  boxWidth = 0;
  boxHeight = 0;
  roCallback = null;
  roDisconnected = false;
  chartInstance.setOption.mockReset();
  chartInstance.dispose.mockClear();
  initMock.mockClear();
  registerMock.mockClear();
  unregisterMock.mockClear();
  chartInstance.resize.mockClear();
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  // The renderer measures via getBoundingClientRect; drive it from the stub.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    () => ({ width: boxWidth, height: boxHeight }) as DOMRect,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ChartRenderer deferred-init lifecycle', () => {
  test('does not init while the container has zero area, inits once when visible, resizes on later change', async () => {
    const { unmount } = render(<ChartRenderer artifact={artifact} />);

    // Content fetched + parsed, but the box is still 0×0 (display:none pane).
    await waitFor(() => expect(roCallback).not.toBeNull());
    roCallback!();
    expect(initMock).not.toHaveBeenCalled();

    // Becomes visible → exactly one init + one registration.
    boxWidth = 640;
    boxHeight = 480;
    roCallback!();
    await waitFor(() => expect(initMock).toHaveBeenCalledTimes(1));
    expect(registerMock).toHaveBeenCalledWith('c1', chartInstance);

    // A later size change re-fits the existing chart, no second init.
    boxWidth = 800;
    roCallback!();
    expect(chartInstance.resize).toHaveBeenCalledTimes(1);
    expect(initMock).toHaveBeenCalledTimes(1);

    // Unmount unregisters by identity (the very instance it registered).
    unmount();
    expect(unregisterMock).toHaveBeenCalledWith('c1', chartInstance);
  });

  test('a setOption throw surfaces an error banner, disposes the leaked instance, does not register, and stops re-throwing', async () => {
    // echarts.init succeeds, setOption throws — a malformed option is this
    // component's documented, expected failure. The instance exists but was
    // never handed to `chart`, so cleanup's dispose can't reach it.
    chartInstance.setOption.mockImplementationOnce(() => {
      throw new Error('bad option');
    });
    render(<ChartRenderer artifact={artifact} />);
    await waitFor(() => expect(roCallback).not.toBeNull());

    boxWidth = 640;
    boxHeight = 480;
    roCallback!();

    await waitFor(() => expect(screen.getByText(/Chart failed: Error: bad option/)).toBeTruthy());
    expect(chartInstance.dispose).toHaveBeenCalledTimes(1); // leaked instance released
    expect(registerMock).not.toHaveBeenCalled(); // never took ownership

    // observer.disconnect() ran, so a later resize does NOT re-init/re-throw.
    boxWidth = 800;
    roCallback!();
    expect(initMock).toHaveBeenCalledTimes(1);
  });
});
