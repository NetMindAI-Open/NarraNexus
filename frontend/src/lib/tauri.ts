/**
 * @file_name: tauri.ts
 * @author: NexusAgent
 * @date: 2026-04-13
 * @description: Thin wrapper for Tauri IPC calls used by dashboard.
 *
 * We invoke via the global `window.__TAURI_INTERNALS__.invoke` that Tauri v2
 * injects — no npm package dependency. Web-mode callers are safe no-ops.
 */

type TauriInvoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

interface TauriInternalsGlobal {
  invoke: TauriInvoke;
}

interface TauriEventGlobal {
  listen: (
    event: string,
    handler: (ev: unknown) => void,
  ) => Promise<() => void>;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: TauriInternalsGlobal;
    __TAURI__?: {
      event?: TauriEventGlobal;
      core?: { invoke?: TauriInvoke };
    };
  }
}

export function isTauri(): boolean {
  if (typeof window === 'undefined') return false;
  if (typeof window.__TAURI_INTERNALS__ !== 'undefined') return true;
  if (typeof window.__TAURI__ !== 'undefined') return true;
  try {
    if (window.location.protocol === 'tauri:') return true;
    if (window.location.hostname === 'tauri.localhost') return true;
  } catch {
    // ignore
  }
  return false;
}

function _getInvoke(): TauriInvoke | null {
  if (typeof window === 'undefined') return null;
  if (window.__TAURI_INTERNALS__?.invoke) return window.__TAURI_INTERNALS__.invoke;
  if (window.__TAURI__?.core?.invoke) return window.__TAURI__.core.invoke;
  return null;
}

export async function setTrayBadge(count: number): Promise<void> {
  if (!isTauri()) return;
  const clamped = Math.max(0, Math.min(999, Math.floor(count)));
  const invoke = _getInvoke();
  if (!invoke) return;
  try {
    await invoke('set_tray_badge', { count: clamped });
  } catch {
    // Tray is cosmetic — swallow.
  }
}

/**
 * Subscribe to a Tauri window event (e.g. "tauri://blur", "tauri://focus").
 * Returns an unsubscribe function, or null if not running in Tauri.
 */
export async function listenTauri(
  event: string,
  handler: (ev: unknown) => void,
): Promise<(() => void) | null> {
  if (!isTauri()) return null;
  const listener = window.__TAURI__?.event?.listen;
  if (!listener) return null;
  try {
    return await listener(event, handler);
  } catch {
    return null;
  }
}
