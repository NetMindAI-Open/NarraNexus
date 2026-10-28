/**
 * @file_name: ChunkErrorBoundary.test.tsx
 * @description: The route-level boundary distinguishes a stale-chunk crash (a
 * deploy artifact — auto-recover once + "new version, refresh") from a real
 * render bug (no recovery — "something went wrong"), and never blank-white-
 * screens. The recover action is injected so BOTH branches are asserted (the
 * load-bearing `if (chunk)` — not just the rendered copy).
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RELOAD_GUARD_KEY } from '@/lib/chunkReload';
import { onUiError, recentUiErrors, resetErrorSink } from '@/platform/errorSink';

import { ChunkErrorBoundary } from '../ChunkErrorBoundary';

function ChunkBoom(): never {
  throw new Error('Failed to fetch dynamically imported module: /assets/Foo-abc123.js');
}
function RealBug(): never {
  throw new Error("Cannot read properties of undefined (reading 'map')");
}

beforeEach(() => {
  // Clear the once-per-session guard so a chunk crash CAN trigger recovery.
  // IMPORTANT: every chunk-crash test below MUST pass a `recover` spy — with
  // the guard cleared, an un-injected chunk test would hit the real
  // window.location.reload() (jsdom "Not implemented: navigation").
  window.sessionStorage.removeItem(RELOAD_GUARD_KEY);
});

// The sink is a module singleton shared by every test file: leave it clean.
afterEach(() => resetErrorSink());

function silenceConsole() {
  const e = vi.spyOn(console, 'error').mockImplementation(() => {});
  const w = vi.spyOn(console, 'warn').mockImplementation(() => {});
  return () => {
    e.mockRestore();
    w.mockRestore();
  };
}

describe('ChunkErrorBoundary', () => {
  test('a stale-chunk crash auto-recovers once AND shows the "new version" prompt', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    expect(recover).toHaveBeenCalledTimes(1); // the load-bearing `if (chunk)` branch
    expect(screen.getByText(/new version/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy();
    restore();
  });

  test('a real render bug does NOT recover and shows a neutral error', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><RealBug /></ChunkErrorBoundary>);
    expect(recover).not.toHaveBeenCalled(); // must NOT auto-reload a real bug (would loop)
    expect(screen.getByText(/something went wrong/i)).toBeTruthy();
    expect(screen.queryByText(/new version/i)).toBeNull();
    restore();
  });

  test('recovers at most once per session', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    expect(recover).toHaveBeenCalledTimes(1); // guard blocks the second
    restore();
  });

  test('reports a stale-chunk crash to the error sink as kind "chunk"', () => {
    const restore = silenceConsole();
    const seen = vi.fn();
    onUiError(seen);
    render(<ChunkErrorBoundary recover={() => {}}><ChunkBoom /></ChunkErrorBoundary>);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].kind).toBe('chunk');
    expect(seen.mock.calls[0][0].error.message).toMatch(/dynamically imported module/);
    restore();
  });

  test('reports a real render bug to the error sink as kind "render", with the component stack', () => {
    const restore = silenceConsole();
    render(<ChunkErrorBoundary recover={() => {}}><RealBug /></ChunkErrorBoundary>);
    const reports = recentUiErrors();
    expect(reports).toHaveLength(1);
    expect(reports[0].kind).toBe('render');
    expect(reports[0].source).toBe('shell');
    expect(reports[0].componentStack).toMatch(/RealBug/);
    restore();
  });

  test('renders children unchanged when nothing throws', () => {
    render(<ChunkErrorBoundary><span>all good</span></ChunkErrorBoundary>);
    expect(screen.getByText('all good')).toBeTruthy();
  });
});
