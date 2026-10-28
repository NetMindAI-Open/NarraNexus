/**
 * @file_name: BrowserSettings.test.tsx
 * @description: Contract for Settings → Browser.
 *
 * The reason this section exists at all is a bug report from 2026-09-22: the
 * agent's "no browser installed" refusal told the user to install it in
 * Settings → Browser, and there was no such section. The agent was right and
 * the app was missing the destination — which, from the user's seat, looks
 * like the agent inventing instructions.
 *
 * So the first test here is simply that the install control is reachable.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/stores/runtimeStore', () => ({ getApiBaseUrl: () => '' }));

import BrowserSettings, { type RuntimeStatus } from '../BrowserSettings';

const manual = {
  root: '/runtime with spaces', shell: 'posix' as const, argv: ['installer-fixture'],
  command: 'installer-owned command fixture', status_command: 'installer-owned status fixture',
  cancel_command: 'installer-owned cancel fixture', manifest_url: 'https://manifest.example.test', download_host: null,
  mirror_flags: { manifest_url: '--manifest-url', download_host: '--download-host' },
};

const absent: RuntimeStatus = {
  state: 'absent', reason: 'no-executable', version: null, executable: null, progress: null,
};
const broken: RuntimeStatus = {
  state: 'absent', reason: 'probe-failed', version: null, executable: '/x/chrome', progress: null,
};
const ready: RuntimeStatus = {
  state: 'ready', reason: 'ready', version: 'Chromium 152', executable: '/x/chrome', progress: null,
};
const installing: RuntimeStatus = {
  ...absent, state: 'installing', reason: 'installing',
  progress: { phase: 'downloading', bytes_done: 25, bytes_total: 100, percent: 25 },
};

const managedChoice: RuntimeStatus = {
  ...ready, selection: { source: 'managed', mode: 'headless', editable: true, system_executable: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' },
};

test('selecting installed Chrome waits for persistence and reports separate sign-ins', async () => {
  const pending = deferred<RuntimeStatus>();
  const changeSource = vi.fn(() => pending.promise);
  render(<BrowserSettings fetchStatus={async () => managedChoice} changeSource={changeSource} />);
  const choice = await screen.findByTestId('browser-runtime-source');
  fireEvent.change(choice, { target: { value: 'system' } });
  expect(changeSource).toHaveBeenCalledWith('system');
  expect(choice).toBeDisabled();
  expect(choice).toHaveValue('managed');
  await act(async () => pending.resolve({ ...managedChoice, version: 'Google Chrome 153',
    selection: { ...managedChoice.selection!, source: 'system' } }));
  expect(choice).toHaveValue('system');
  expect(choice).not.toBeDisabled();
  expect(screen.getByTestId('browser-source-effect').textContent).toMatch(/new browser sessions/i);
  expect(screen.getByTestId('browser-source-effect').textContent).toMatch(/separate sign-ins/i);
});

test('failed Chrome selection keeps the current source and shows the server reason', async () => {
  render(<BrowserSettings fetchStatus={async () => managedChoice}
    changeSource={async () => { throw new Error('Chrome could not start'); }} />);
  const choice = await screen.findByTestId('browser-runtime-source');
  fireEvent.change(choice, { target: { value: 'system' } });
  expect(await screen.findByTestId('browser-settings-error')).toHaveTextContent('Chrome could not start');
  expect(choice).toHaveValue('managed');
});

test('a missing selected system browser directs selection instead of downloading another browser', async () => {
  render(<BrowserSettings fetchStatus={async () => ({ ...absent,
    selection: { source: 'system', mode: 'headless', editable: true, system_executable: null } })} />);
  await screen.findByTestId('browser-runtime-source');
  expect(screen.queryByTestId('browser-settings-install')).toBeNull();
  expect(screen.getByTestId('browser-settings-state').textContent).toMatch(/Google Chrome/i);
});

test('headed mode waits for persistence and prevents conflicting changes', async () => {
  const pending = deferred<RuntimeStatus>();
  const changeMode = vi.fn(() => pending.promise);
  render(<BrowserSettings fetchStatus={async () => managedChoice} changeMode={changeMode} />);
  const toggle = await screen.findByRole('switch', { name: /Show browser window/i });
  fireEvent.click(toggle);
  expect(changeMode).toHaveBeenCalledWith('headed');
  expect(toggle).toBeDisabled();
  expect(toggle).toHaveAttribute('aria-checked', 'false');
  expect(screen.getByTestId('browser-runtime-source')).toBeDisabled();
  expect(screen.getByTestId('browser-settings-recheck')).toBeDisabled();
  await act(async () => pending.resolve({ ...managedChoice,
    selection: { ...managedChoice.selection!, mode: 'headed' } }));
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  expect(toggle).not.toBeDisabled();
  expect(screen.getByTestId('browser-mode-effect')).toHaveTextContent(/sign-ins are kept/i);
});

test('mode save failure retains the saved value and reports the reason', async () => {
  render(<BrowserSettings fetchStatus={async () => managedChoice}
    changeMode={async () => { throw new Error('Could not save browser mode preference'); }} />);
  const toggle = await screen.findByRole('switch', { name: /Show browser window/i });
  fireEvent.click(toggle);
  expect(await screen.findByTestId('browser-settings-error')).toHaveTextContent('Could not save browser mode preference');
  expect(toggle).toHaveAttribute('aria-checked', 'false');
  expect(toggle).not.toBeDisabled();
});

test('cloud cannot expose local window controls', async () => {
  render(<BrowserSettings fetchStatus={async () => ({ ...managedChoice,
    selection: { ...managedChoice.selection!, editable: false } })} />);
  await screen.findByTestId('browser-settings-ready');
  expect(screen.queryByRole('switch', { name: /Show browser window/i })).toBeNull();
});

afterEach(() => { cleanup(); vi.useRealTimers(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

test('an uninstalled runtime shows a reachable install button', async () => {
  render(<BrowserSettings fetchStatus={async () => absent} startInstall={async () => ({ ok: true, error: null })} />);
  await waitFor(() => expect(screen.getByTestId('browser-settings-install')).toBeTruthy());
});

test('clicking install actually starts the install', async () => {
  const startInstall = vi.fn(async () => ({ ok: true, error: null }));
  render(<BrowserSettings fetchStatus={async () => absent} startInstall={startInstall} />);
  await waitFor(() => screen.getByTestId('browser-settings-install'));
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await waitFor(() => expect(startInstall).toHaveBeenCalled());
});

test('a broken runtime offers re-install and says why', async () => {
  render(<BrowserSettings fetchStatus={async () => broken} startInstall={async () => ({ ok: true, error: null })} />);
  await waitFor(() => screen.getByTestId('browser-settings-install'));
  expect(screen.getByTestId('browser-settings-install').textContent).toMatch(/Re-install/i);
  expect(screen.getByTestId('browser-settings-state').textContent).toMatch(/will not start/i);
});

test('an installed runtime reports itself instead of offering to install again', async () => {
  render(<BrowserSettings fetchStatus={async () => ready} startInstall={async () => ({ ok: true, error: null })} />);
  await waitFor(() => expect(screen.getByTestId('browser-settings-ready')).toBeTruthy());
  expect(screen.queryByTestId('browser-settings-install')).toBeNull();
});

test('an install failure is shown, not swallowed', async () => {
  render(
    <BrowserSettings
      fetchStatus={async () => absent}
      startInstall={async () => ({ ok: false, error: 'mirror unreachable' })}
    />,
  );
  await waitFor(() => screen.getByTestId('browser-settings-install'));
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await waitFor(() =>
    expect(screen.getByTestId('browser-settings-error').textContent).toContain('mirror unreachable'));
});

test('a status endpoint that fails still offers a way forward', async () => {
  // Originally this asserted the INSTALL button appears on a failed status
  // call. That encoded the 2026-09-22 bug as the expectation: a rejected
  // request became "not installed", so the user was offered a button whose
  // request was about to be rejected the same way. The way forward is a
  // retry plus the actual reason.
  render(
    <BrowserSettings
      fetchStatus={async () => { throw new Error('backend down'); }}
      startInstall={async () => ({ ok: true, error: null })}
    />,
  );
  await waitFor(() => expect(screen.getByTestId('browser-settings-retry')).toBeTruthy());
  expect(screen.getByTestId('browser-settings-load-error').textContent).toContain('backend down');
});

test('an unknown download total does not render an invented percentage', async () => {
  render(
    <BrowserSettings
      fetchStatus={async () => ({ ...absent, state: 'installing', reason: 'installing',
        progress: { phase: 'downloading', bytes_done: 9, bytes_total: null, percent: null } })}
      startInstall={async () => ({ ok: true, error: null })}
    />,
  );
  await waitFor(() => expect(screen.getByTestId('browser-settings-progress')).toBeTruthy());
  expect(screen.getByTestId('browser-settings-progress').textContent).not.toMatch(/%/);
});

// ── the 2026-09-22 bug: a rejected request must not look like "not installed" ──

test('a failing status call is reported as a fault, not as "not installed"', async () => {
  // The raw fetch this component used to do missed the identity headers local
  // mode requires. The 401 was caught and turned into `absent`, so the page
  // said "not installed yet" about a browser that WAS installed, and offered
  // a button whose request was rejected the same way.
  render(
    <BrowserSettings
      fetchStatus={async () => { throw new Error('Missing X-User-Id header'); }}
      startInstall={async () => ({ ok: true, error: null })}
    />,
  );
  await waitFor(() => expect(screen.getByTestId('browser-settings-load-error')).toBeTruthy());
  expect(screen.getByTestId('browser-settings-load-error').textContent).toContain('X-User-Id');
  expect(screen.queryByTestId('browser-settings-state')).toBeNull();
});

test('the failure offers a retry rather than a dead end', async () => {
  let fail = true;
  render(
    <BrowserSettings
      fetchStatus={async () => {
        if (fail) { fail = false; throw new Error('backend down'); }
        return ready;
      }}
      startInstall={async () => ({ ok: true, error: null })}
    />,
  );
  await waitFor(() => screen.getByTestId('browser-settings-retry'));
  fireEvent.click(screen.getByTestId('browser-settings-retry'));
  await waitFor(() => expect(screen.getByTestId('browser-settings-ready')).toBeTruthy());
});

test('an install whose request never reached the handler still says something', async () => {
  // `ok` absent (not false) is what an auth rejection or a proxy error looks
  // like after JSON parsing. Treating it as "nothing to report" is what made
  // the button appear dead.
  render(
    <BrowserSettings
      fetchStatus={async () => absent}
      startInstall={async () => ({} as { ok: boolean; error: string | null })}
    />,
  );
  await waitFor(() => screen.getByTestId('browser-settings-install'));
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await waitFor(() => expect(screen.getByTestId('browser-settings-error')).toBeTruthy());
});

test('an install that throws is surfaced, not swallowed', async () => {
  render(
    <BrowserSettings
      fetchStatus={async () => absent}
      startInstall={async () => { throw new Error('API error: 401'); }}
    />,
  );
  await waitFor(() => screen.getByTestId('browser-settings-install'));
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await waitFor(() =>
    expect(screen.getByTestId('browser-settings-error').textContent).toContain('401'));
});

test('a pending start remains busy even if status has not reached installing yet', async () => {
  vi.useFakeTimers();
  const request = deferred<{ ok: boolean; error: string | null }>();
  const startInstall = vi.fn(() => request.promise);
  render(<BrowserSettings fetchStatus={async () => absent} startInstall={startInstall} />);
  await act(async () => {});
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
  expect(screen.queryByTestId('browser-settings-install')).not.toBeInTheDocument();
  expect(screen.getByTestId('browser-settings-progress')).toHaveTextContent(/starting|preparing/i);
  expect(startInstall).toHaveBeenCalledTimes(1);
  await act(async () => { request.resolve({ ok: false, error: 'download unavailable' }); });
  expect(screen.getByRole('alert')).toHaveTextContent('download unavailable');
  expect(screen.getByTestId('browser-settings-install')).toBeEnabled();
});

test('cancel is pending until the runtime confirms installation stopped', async () => {
  vi.useFakeTimers();
  const request = deferred<{ ok: boolean }>();
  const cancelInstall = vi.fn(() => request.promise);
  const fetchStatus = vi.fn().mockResolvedValue(installing);
  render(<BrowserSettings fetchStatus={fetchStatus} cancelInstall={cancelInstall} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
  expect(cancelInstall).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
  await act(async () => { request.resolve({ ok: true }); });
  expect(screen.getByTestId('browser-settings-progress')).toHaveTextContent(/cancell|cancellation/i);
  expect(screen.queryByTestId('browser-settings-install')).not.toBeInTheDocument();
  fetchStatus.mockResolvedValue(absent);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByTestId('browser-settings-install')).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent(/cancelled/i);
});

test.each(['response', 'exception'])('a cancellation %s failure is announced and allows retry', async (kind) => {
  const cancelInstall = vi.fn();
  if (kind === 'response') cancelInstall.mockResolvedValue({ ok: false, error: 'cannot cancel' });
  else cancelInstall.mockRejectedValue(new Error('cannot cancel'));
  render(<BrowserSettings fetchStatus={async () => installing} cancelInstall={cancelInstall} />);
  fireEvent.click(await screen.findByRole('button', { name: /cancel/i }));
  expect(await screen.findByRole('alert')).toHaveTextContent('cannot cancel');
  expect(screen.getByRole('button', { name: /cancel/i })).toBeEnabled();
});

test('a transient progress failure keeps cancellation available and recovers automatically', async () => {
  vi.useFakeTimers();
  const fetchStatus = vi.fn().mockResolvedValueOnce(installing)
    .mockRejectedValueOnce(new Error('connection interrupted')).mockResolvedValue(ready);
  render(<BrowserSettings fetchStatus={fetchStatus} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole('alert')).toHaveTextContent('connection interrupted');
  expect(screen.getByRole('button', { name: /cancel/i })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByTestId('browser-settings-ready')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('slow progress polls never overlap', async () => {
  vi.useFakeTimers();
  const request = deferred<RuntimeStatus>();
  const fetchStatus = vi.fn().mockResolvedValueOnce(installing).mockReturnValue(request.promise);
  render(<BrowserSettings fetchStatus={fetchStatus} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(fetchStatus).toHaveBeenCalledTimes(2);
  await act(async () => { request.resolve(ready); });
});

test('switching status sources ignores a late response from the old source', async () => {
  const request = deferred<RuntimeStatus>();
  const view = render(<BrowserSettings fetchStatus={() => request.promise} />);
  view.rerender(<BrowserSettings fetchStatus={async () => ready} />);
  await screen.findByTestId('browser-settings-ready');
  await act(async () => { request.resolve(absent); });
  expect(screen.getByTestId('browser-settings-ready')).toBeInTheDocument();
});

test.each(['extracting', 'verifying'])('%s is not presented as a finished download', async (phase) => {
  render(<BrowserSettings fetchStatus={async () => ({ ...installing,
    progress: { ...installing.progress!, phase, percent: 100 },
  })} />);
  const progress = await screen.findByTestId('browser-settings-progress');
  expect(progress).toHaveTextContent(new RegExp(phase, 'i'));
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(progress).not.toHaveTextContent('100%');
});

test('download progress has a meaningful accessible name and numeric value', async () => {
  render(<BrowserSettings fetchStatus={async () => installing} />);
  expect(await screen.findByRole('progressbar', { name: /download/i })).toHaveAttribute('aria-valuenow', '25');
});

test('unknown download progress uses indeterminate status instead of a full bar', async () => {
  render(<BrowserSettings fetchStatus={async () => ({ ...installing,
    progress: { ...installing.progress!, bytes_total: null, percent: null },
  })} />);
  await screen.findByTestId('browser-settings-progress');
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveAccessibleName(/download/i);
});

test('an absent runtime exposes the actual manual recovery commands', async () => {
  render(<BrowserSettings fetchStatus={async () => ({ ...absent, manual_install: manual })} />);
  expect(await screen.findByRole('textbox')).toHaveValue(manual.command);
  expect(screen.getByText(manual.root)).toBeInTheDocument();
});

test('an installation failure keeps installer-supplied recovery even when the next status lookup fails', async () => {
  const fetchStatus = vi.fn().mockResolvedValueOnce(absent).mockRejectedValue(new Error('status unavailable'));
  render(<BrowserSettings fetchStatus={fetchStatus}
    startInstall={async () => ({ ok: false, error: 'download failed', manual_install: manual })} />);
  fireEvent.click(await screen.findByTestId('browser-settings-install'));
  expect(await screen.findByRole('textbox')).toHaveValue(manual.command);
  expect(screen.getByTestId('browser-settings-error')).toHaveTextContent('download failed');
});

test('manual installation controls disappear after a fresh ready status', async () => {
  const fetchStatus = vi.fn().mockResolvedValue({ ...absent, manual_install: manual });
  render(<BrowserSettings fetchStatus={fetchStatus} startInstall={async () => {
    fetchStatus.mockResolvedValue(ready);
    return { ok: true, error: null };
  }} />);
  await screen.findByRole('textbox');
  fireEvent.click(screen.getByTestId('browser-settings-install'));
  await screen.findByTestId('browser-settings-ready');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});
