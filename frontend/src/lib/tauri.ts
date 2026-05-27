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

/**
 * Drain the URL Rust stashed when the OS handed us a `narranexus://` link
 * before the React mount finished. The Rust handler also emits a
 * "deep-link-received" event for the already-mounted (hot) case, so the
 * App-level listener wires both: this on first mount, the event for live
 * URLs. See tauri/src-tauri/src/commands/deep_link.rs for the buffer
 * rationale (Tauri events fired before any listener exists are dropped).
 *
 * Returns the URL string or null when no pending URL / not in Tauri /
 * the IPC call failed.
 */
export async function consumePendingDeepLink(): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  try {
    const result = await invoke('consume_pending_deep_link');
    return typeof result === 'string' && result.length > 0 ? result : null;
  } catch {
    return null;
  }
}

/**
 * Manually trigger an app update check (desktop only). The Rust command checks
 * the release endpoint, downloads + installs a newer signed build if present,
 * and returns a status string: `'up_to_date'` or `'installed:<version>'` (the
 * installed update applies on restart). Throws on failure so the caller can
 * surface the message. Returns null when not running in Tauri.
 *
 * NOTE: the app also auto-checks on startup (Rust `run_startup_update_check`);
 * this is the explicit "Check for updates" button path.
 */
export async function checkForUpdates(): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  return (await invoke('check_and_install_update')) as string;
}

/**
 * Open `url` in the OS default browser via the tauri-plugin-shell `open`
 * command. Returns `true` if the open call succeeded, `false` otherwise
 * (no Tauri, no invoke channel, or plugin error).
 *
 * Why this exists: `<a target="_blank">` in the Tauri WKWebView silently
 * does nothing — the webview either swallows the click or tries to load
 * the URL inside itself (CSP / cross-origin blocks it). Routing through
 * shell.open hands the URL to the OS so the user actually lands somewhere.
 * Used by lib/externalLinkInterceptor to intercept all external link
 * clicks app-wide. Web/browser mode is intentionally a no-op (browsers
 * already handle target="_blank" correctly).
 *
 * Capability + plugin wiring (no npm dep needed):
 *   - Rust: tauri-plugin-shell init in tauri/src-tauri/src/lib.rs:33
 *   - Capability: shell:allow-open in capabilities/default.json:8
 *   - Config: "shell": { "open": true } in tauri.conf.json:53-55
 * We invoke `plugin:shell|open` directly via __TAURI__.invoke to keep this
 * file dependency-free (matches the other helpers above).
 */
export async function openExternal(url: string): Promise<boolean> {
  if (!isTauri()) return false;
  const invoke = _getInvoke();
  if (!invoke) return false;
  try {
    await invoke('plugin:shell|open', { path: url });
    return true;
  } catch (err) {
    console.error('[tauri] openExternal failed:', err);
    return false;
  }
}
