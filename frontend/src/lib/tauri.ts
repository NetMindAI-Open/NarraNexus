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
 * Open NetMind's OAuth page (auth.html) in a Rust-created child webview and let
 * the launcher bridge its postMessage result back via the
 * "netmind-oauth-callback" Tauri event. Desktop-only replacement for the
 * browser's window.open popup, which WKWebView blocks. No-op outside Tauri.
 * See tauri/src-tauri/src/commands/netmind_oauth.rs.
 */
export async function openNetmindOAuth(url: string): Promise<void> {
  if (!isTauri()) return;
  const invoke = _getInvoke();
  if (!invoke) return;
  await invoke('open_netmind_oauth', { url });
}

/**
 * Drain the buffered NetMind OAuth result (URI-encoded or plain JSON string),
 * or null if none yet. The frontend polls this after openNetmindOAuth — a
 * poll-based delivery that (unlike a live Tauri event) never depends on
 * window.__TAURI__ being present. See commands/netmind_oauth.rs.
 */
export async function takeNetmindOAuthResult(): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  try {
    const r = await invoke('take_netmind_oauth_result');
    return typeof r === 'string' && r.length > 0 ? r : null;
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

// ── Unified auto-updater ────────────────────────────────────────────────
//
// One Rust state machine drives three UI surfaces (global banner, Settings
// panel, tray menu label). Frontend just kicks the pipeline and mirrors
// state events; all logic lives in `commands/updater.rs`. See
// stores/updaterStore.ts for the consumer side.

/**
 * Mirror of the Rust `UpdaterState` enum. Kept synchronised by hand —
 * change Rust → change here. Stored in lib/tauri.ts (not the store) so
 * any consumer can import the type without pulling Zustand.
 */
export type UpdaterState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'up_to_date'; current: string; checked_at: number }
  | { kind: 'available'; version: string; notes: string | null }
  | {
      kind: 'downloading';
      downloaded: number;
      total: number | null;
      percent: number | null;
    }
  | { kind: 'installing'; version: string }
  | { kind: 'ready'; version: string }
  | { kind: 'failed'; stage: 'check' | 'download' | 'install'; error: string };

/**
 * Kick the unified updater pipeline: check → (if available) download →
 * install → land at `ready`. Returns immediately; progress arrives via
 * the `updater:state` Tauri event (subscribe with `listenUpdaterState`).
 * Re-entrancy is guarded Rust-side, so calling while a pipeline is in
 * flight is a harmless no-op. No-op in web/browser mode.
 */
export async function kickUpdaterCheck(): Promise<void> {
  if (!isTauri()) return;
  const invoke = _getInvoke();
  if (!invoke) return;
  await invoke('updater_check');
}

/**
 * Snapshot the current updater state. Used by the store on mount to
 * cover the case where a startup-auto pipeline already transitioned
 * before React attached its event listener.
 */
export async function getUpdaterState(): Promise<UpdaterState | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  try {
    return (await invoke('updater_get_state')) as UpdaterState;
  } catch (e) {
    console.warn('[tauri.getUpdaterState] failed:', e);
    return null;
  }
}

/**
 * Restart the app to apply a downloaded update. Frontend gates the
 * button on `state.kind === 'ready'`; the Rust side does not validate,
 * so call this only when the state machine is actually Ready.
 */
export async function restartForUpdate(): Promise<void> {
  if (!isTauri()) return;
  const invoke = _getInvoke();
  if (!invoke) return;
  await invoke('updater_restart');
}

/**
 * Subscribe to live updater state changes. Returns an unsubscribe
 * function; null when not in Tauri / event channel missing. The
 * `updaterStore.init()` is the only intended caller.
 */
export async function listenUpdaterState(
  handler: (next: UpdaterState) => void,
): Promise<(() => void) | null> {
  if (!isTauri()) return null;
  const listener = window.__TAURI__?.event?.listen;
  if (!listener) return null;
  try {
    return await listener('updater:state', (ev: unknown) => {
      // Tauri event payload type: { event, payload, id, windowLabel }
      const payload = (ev as { payload?: UpdaterState }).payload;
      if (payload) handler(payload);
    });
  } catch (e) {
    console.warn('[tauri.listenUpdaterState] subscribe failed:', e);
    return null;
  }
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

/**
 * Save a backend URL to the OS Downloads folder via the Rust side.
 *
 * Why this exists
 *   On both local surfaces (dmg and `bash run.sh`) the standard
 *   `<a href download>` pattern is broken:
 *     - dmg: WKWebView's mixed-content blocker kills HTTP navigations
 *       initiated from the HTTPS `tauri.localhost` origin.
 *     - browser (Vite :5173 → backend :8000): cross-origin, so the
 *       `download` attribute is silently ignored; workspace files also
 *       need `X-User-Id` / `Authorization` headers that `<a>` can't attach.
 *   Rust-originated HTTP is immune to WKWebView's mixed-content rules. This
 *   helper invokes `download_file_via_backend` which fetches the bytes via
 *   reqwest, saves them into ~/Downloads (with collision avoidance), and
 *   returns the absolute path so the UI can display it.
 *
 * Returns
 *   - Absolute path string on success.
 *   - null when not running in Tauri / IPC channel missing.
 *   - Throws a string error when the Rust side returns an error (HTTP
 *     failure, filesystem write error, etc.).
 */
export async function downloadFileViaTauri(
  url: string,
  filename: string,
  headers?: Record<string, string>,
): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  return (await invoke('download_file_via_backend', { url, filename, headers })) as string;
}

interface ArtifactBytesIpc {
  status: number;
  content_type: string;
  bytes_base64: string;
}

/**
 * Fetch an artifact's bytes through the Rust side instead of `fetch()`.
 *
 * Why this exists
 *   In the dmg the webview origin is `https://tauri.localhost` (HTTPS) and
 *   the backend serves `http://localhost:8000` (HTTP). WKWebView treats
 *   the http resource as "active mixed content" and blocks it silently —
 *   iframe loads AND `fetch()` from JS. The result was a white artifact
 *   tab (P0 reported 2026-05-27).
 *
 *   HTTP requests originated by Rust are not subject to the WKWebView
 *   mixed-content blocker. This helper invokes the
 *   `fetch_artifact_via_backend` Tauri command which uses `reqwest` from
 *   the Rust process to pull the artifact bytes, ships them back over the
 *   IPC channel as base64, and we reconstruct a Blob + `blob:` URL the
 *   sandboxed iframe can load same-origin.
 *
 * Returns
 *   - blob: URL on success — caller MUST `URL.revokeObjectURL()` it when
 *     done (typically in the same useEffect's cleanup).
 *   - null when not running in Tauri / IPC channel missing / command
 *     errored / non-200 status. Caller should fall back to a plain
 *     `fetch()` in that case.
 */
export async function fetchArtifactViaTauri(url: string): Promise<string | null> {
  if (!isTauri()) return null;
  const invoke = _getInvoke();
  if (!invoke) return null;
  try {
    const resp = (await invoke('fetch_artifact_via_backend', { url })) as ArtifactBytesIpc;
    if (!resp || resp.status !== 200) {
      console.warn(`[tauri.fetchArtifact] non-200 status: ${resp?.status}`);
      return null;
    }
    const binary = atob(resp.bytes_base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const blob = new Blob([bytes], { type: resp.content_type || 'application/octet-stream' });
    return URL.createObjectURL(blob);
  } catch (e) {
    console.error('[tauri.fetchArtifact] IPC failed:', e);
    return null;
  }
}
