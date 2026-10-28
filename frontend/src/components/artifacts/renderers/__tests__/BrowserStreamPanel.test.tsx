/**
 * @file_name: BrowserStreamPanel.test.tsx
 * @description: Behaviour contract for the live browser panel.
 *
 * Locks the three things that decide whether a user can get themselves
 * unstuck:
 *   1. an unusable runtime shows an install card, never a blank canvas;
 *   2. "not installed" and "installed but broken" say different things —
 *      collapsing them throws away the user's only clue;
 *   3. watching is free, driving is exclusive: input is not forwarded until
 *      the user explicitly takes control.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/stores/runtimeStore', () => ({
  getWsBaseUrl: () => 'ws://127.0.0.1:8000',
  getApiBaseUrl: () => '',
}));

import BrowserStreamPanel, { type RuntimeStatus } from '../BrowserStreamPanel';

const sent: string[] = [];

class FakeWebSocket {
  static last: FakeWebSocket | null = null;
  static OPEN = 1;
  readyState = 1;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeWebSocket.last = this; }
  send(raw: string) { sent.push(raw); }
  close() { /* no-op */ }
}

beforeEach(() => {
  sent.length = 0;
  FakeWebSocket.last = null;
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
});

const ready: RuntimeStatus = {
  state: 'ready', reason: 'ready', version: 'Chromium 152', executable: '/x/chrome', progress: null,
};
const absent: RuntimeStatus = {
  state: 'absent', reason: 'no-executable', version: null, executable: null, progress: null,
};
const broken: RuntimeStatus = {
  state: 'absent', reason: 'probe-failed', version: null, executable: '/x/chrome', progress: null,
};

function renderPanel(status: RuntimeStatus, opts: { sessionId?: string | null } = {}) {
  const startInstall = vi.fn(async () => ({ ok: true, error: null }));
  // `??` would turn an explicit null back into 's1' — the case this helper
  // exists to exercise. Check for the key instead.
  const sessionId = 'sessionId' in opts ? opts.sessionId : 's1';
  render(
    <BrowserStreamPanel
      sessionId={sessionId}
      fetchStatus={async () => status}
      startInstall={startInstall}
    />,
  );
  return { startInstall };
}

describe('runtime gating', () => {
  test('a live session stays visible when the runtime for new sessions is unavailable', async () => {
    renderPanel(absent);
    await screen.findByTestId('browser-install-card');
    await waitFor(() => expect(FakeWebSocket.last).not.toBeNull());
    act(() => FakeWebSocket.last?.onmessage?.({ data: JSON.stringify({
      type: 'hello', control: { holder: 'user', can_control: true },
    }) }));
    expect(await screen.findByTestId('browser-panel')).toBeTruthy();
    expect(screen.queryByTestId('browser-install-card')).toBeNull();
  });

  test('missing selected Chrome asks for a source change instead of offering an unrelated download', async () => {
    renderPanel({ ...absent, selection: { source: 'system', mode: 'headless', editable: true, system_executable: null } });
    const card = await screen.findByTestId('browser-install-card');
    expect(card.textContent).toMatch(/Google Chrome/);
    expect(screen.queryByTestId('browser-install-button')).toBeNull();
  });

  test('absent runtime renders the install card, not a canvas', async () => {
    renderPanel(absent);
    await waitFor(() => expect(screen.getByTestId('browser-install-card')).toBeTruthy());
    expect(screen.queryByTestId('browser-canvas')).toBeNull();
  });

  test('a broken runtime offers re-install, not install', async () => {
    renderPanel(broken);
    await waitFor(() => expect(screen.getByTestId('browser-install-button')).toBeTruthy());
    expect(screen.getByTestId('browser-install-button').textContent).toMatch(/Re-install/i);
  });

  test('a missing runtime offers install', async () => {
    renderPanel(absent);
    await waitFor(() => expect(screen.getByTestId('browser-install-button')).toBeTruthy());
    expect(screen.getByTestId('browser-install-button').textContent).toMatch(/^Install/i);
  });

  test('clicking install starts it', async () => {
    const { startInstall } = renderPanel(absent);
    await waitFor(() => screen.getByTestId('browser-install-button'));
    fireEvent.click(screen.getByTestId('browser-install-button'));
    await waitFor(() => expect(startInstall).toHaveBeenCalled());
  });

  test('an install failure is shown rather than swallowed', async () => {
    render(
      <BrowserStreamPanel
        sessionId="s1"
        fetchStatus={async () => absent}
        startInstall={async () => ({ ok: false, error: 'mirror unreachable' })}
      />,
    );
    await waitFor(() => screen.getByTestId('browser-install-button'));
    fireEvent.click(screen.getByTestId('browser-install-button'));
    await waitFor(() =>
      expect(screen.getByTestId('browser-install-error').textContent).toContain('mirror unreachable'),
    );
  });

  test('an indeterminate download renders a bar without inventing a percentage', async () => {
    renderPanel({ ...absent, state: 'installing', reason: 'installing',
      progress: { phase: 'downloading', bytes_done: 10, bytes_total: null, percent: null } });
    await waitFor(() => expect(screen.getByTestId('browser-install-progress')).toBeTruthy());
    expect(screen.getByTestId('browser-install-progress').textContent).not.toMatch(/%/);
  });

  test('a known total renders the percentage', async () => {
    renderPanel({ ...absent, state: 'installing', reason: 'installing',
      progress: { phase: 'downloading', bytes_done: 5, bytes_total: 10, percent: 50 } });
    await waitFor(() =>
      expect(screen.getByTestId('browser-install-progress').textContent).toContain('50%'));
  });
});

describe('streaming and control', () => {
  test('ready runtime with a session renders the canvas', async () => {
    renderPanel(ready);
    await waitFor(() => expect(screen.getByTestId('browser-canvas')).toBeTruthy());
  });

  test('ready runtime without a session says so instead of showing a dead canvas', async () => {
    renderPanel(ready, { sessionId: null });
    await waitFor(() => expect(screen.getByTestId('browser-idle')).toBeTruthy());
  });

  test('the header names who is driving', async () => {
    renderPanel(ready);
    await waitFor(() => expect(screen.getByTestId('browser-holder')).toBeTruthy());
    expect(screen.getByTestId('browser-holder').textContent).toMatch(/Agent is driving/i);
  });

  test('taking control is unavailable until a live frame arrives', async () => {
    renderPanel(ready);
    await waitFor(() => screen.getByTestId('browser-canvas'));

    fireEvent.mouseDown(screen.getByTestId('browser-canvas'), { clientX: 5, clientY: 5 });
    expect(sent.some((m) => m.includes('take_control'))).toBe(false);

    fireEvent.click(screen.getByTestId('browser-control-toggle'));
    expect(screen.getByTestId('browser-control-toggle')).toBeDisabled();
    expect(sent.some((m) => m.includes('input') || m.includes('take_control'))).toBe(false);
  });

  test('a control message from the server updates the header', async () => {
    renderPanel(ready);
    await waitFor(() => screen.getByTestId('browser-canvas'));

    act(() => FakeWebSocket.last!.onmessage!({
      data: JSON.stringify({ type: 'control', control: { holder: 'user', can_control: true } }),
    }));

    await waitFor(() =>
      expect(screen.getByTestId('browser-holder').textContent).toMatch(/You are driving/i));
  });

  test('an undecodable frame does not crash the panel', async () => {
    renderPanel(ready);
    await waitFor(() => screen.getByTestId('browser-canvas'));

    FakeWebSocket.last!.onmessage!({ data: 'not json' });

    expect(screen.getByTestId('browser-canvas')).toBeTruthy();
  });
});
