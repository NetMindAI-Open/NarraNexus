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

/**
 * Trigger Claude Code OAuth login from the desktop app.
 * Spawns `claude auth login` which opens the system browser for OAuth.
 * Returns the result string on success, or throws on failure.
 * No-op (returns null) if not running in Tauri.
 */
export async function triggerClaudeLogin(): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  return (await invoke('trigger_claude_login')) as string;
}

/**
 * Trigger Claude Code logout — revokes the locally cached OAuth
 * credentials. Symmetric to `triggerClaudeLogin`. No-op (returns null)
 * outside Tauri; throws if the spawned CLI exits non-zero.
 */
export async function triggerClaudeLogout(): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  return (await invoke('trigger_claude_logout')) as string;
}

/**
 * SIGTERM the in-flight `claude auth login` child. Used by the
 * settings UI to abort a stuck login when the 600s countdown elapses.
 * Resolves to `true` if a login was actually in flight, `false` if
 * the Rust side had no recorded PID. Outside Tauri returns false.
 */
export async function cancelClaudeLogin(): Promise<boolean> {
  if (!isTauri()) return false;
  const invoke = _getInvoke();
  if (!invoke) return false;
  return (await invoke('cancel_claude_login')) as boolean;
}

/**
 * Check Claude Code login status from the Tauri side.
 * Returns { cli_installed, logged_in } or null if not in Tauri.
 */
export async function getClaudeLoginStatus(): Promise<{
  cli_installed: boolean;
  logged_in: boolean;
} | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  return (await invoke('get_claude_login_status')) as {
    cli_installed: boolean;
    logged_in: boolean;
  };
}
