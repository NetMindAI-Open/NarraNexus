/**
 * @file_name: powerStore.test.ts
 * @description: Behavior contract for the Locked Use (prevent-sleep) store.
 *
 * Key invariants:
 *   - enabling invokes the Tauri set_prevent_sleep command and stores the
 *     confirmed state
 *   - a failed command leaves the state off (no lying toggle)
 *   - disabling invokes with enabled=false
 *   - applyOnStartup re-asserts a persisted "on" state after app restart
 *     (the OS-side caffeinate assertion dies with the previous process)
 *   - outside Tauri everything is a no-op and the state stays off
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

const invokeTauriMock = vi.fn();
const isTauriMock = vi.fn();
vi.mock('@/lib/tauri', () => ({
  invokeTauri: (...args: unknown[]) => invokeTauriMock(...args),
  isTauri: () => isTauriMock(),
}));

import { usePowerStore } from '../powerStore';

beforeEach(() => {
  vi.clearAllMocks();
  isTauriMock.mockReturnValue(true);
  invokeTauriMock.mockResolvedValue(true);
  usePowerStore.setState({ preventSleep: false });
  window.localStorage.clear();
});

describe('setPreventSleep', () => {
  it('enabling invokes the Tauri command and stores the state', async () => {
    await usePowerStore.getState().setPreventSleep(true);

    expect(invokeTauriMock).toHaveBeenCalledWith('set_prevent_sleep', { enabled: true });
    expect(usePowerStore.getState().preventSleep).toBe(true);
  });

  it('a failed command leaves the toggle off', async () => {
    invokeTauriMock.mockRejectedValue(new Error('unsupported'));

    await usePowerStore.getState().setPreventSleep(true);

    expect(usePowerStore.getState().preventSleep).toBe(false);
  });

  it('disabling invokes with enabled=false', async () => {
    await usePowerStore.getState().setPreventSleep(true);
    await usePowerStore.getState().setPreventSleep(false);

    expect(invokeTauriMock).toHaveBeenLastCalledWith('set_prevent_sleep', { enabled: false });
    expect(usePowerStore.getState().preventSleep).toBe(false);
  });

  it('is a no-op outside Tauri', async () => {
    isTauriMock.mockReturnValue(false);

    await usePowerStore.getState().setPreventSleep(true);

    expect(invokeTauriMock).not.toHaveBeenCalled();
    expect(usePowerStore.getState().preventSleep).toBe(false);
  });
});

describe('applyOnStartup', () => {
  it('re-asserts a persisted enabled state', async () => {
    usePowerStore.setState({ preventSleep: true });

    await usePowerStore.getState().applyOnStartup();

    expect(invokeTauriMock).toHaveBeenCalledWith('set_prevent_sleep', { enabled: true });
  });

  it('does nothing when the persisted state is off', async () => {
    await usePowerStore.getState().applyOnStartup();

    expect(invokeTauriMock).not.toHaveBeenCalled();
  });
});
