/**
 * @file_name: download.ts
 * @author: NarraNexus
 * @date: 2026-06-16
 * @description: Cross-surface file download utility.
 *
 * Two download surfaces are broken with plain `<a href download>`:
 *
 * 1. Tauri DMG — the webview origin is `https://tauri.localhost` (HTTPS) while
 *    the backend serves on `http://localhost:8000` (HTTP). WKWebView classifies
 *    the HTTP navigation as "active mixed content" and blocks it silently.
 *    Additionally, the `download` attribute is ignored for cross-origin URLs
 *    in all modern browsers.
 *
 * 2. Local browser (`bash run.sh`, Vite :5173 → backend :8000) — cross-origin
 *    so the `download` attribute is silently ignored (browser navigates instead
 *    of saving). Workspace files also require `X-User-Id` / `Authorization`
 *    headers that `<a>` elements cannot attach → 401.
 *
 * This module provides a single `downloadFile()` function that chooses the
 * right strategy per runtime:
 *   - Tauri: delegate to the Rust `download_file_via_backend` command (saves to
 *     ~/Downloads, returns the absolute path).
 *   - Browser: `fetch()` with auth headers → Blob → object URL → programmatic
 *     `<a>` click → revoke. Works for both local and cloud because `fetch()`
 *     carries auth headers and the response body lands in memory before saving.
 */

import { isTauri, downloadFileViaTauri } from './tauri';

export interface DownloadFileOptions {
  /** Absolute URL of the file to download. */
  url: string;
  /** Suggested filename for the saved file (basename; no path separators). */
  filename: string;
  /**
   * Auth headers to attach to the request.
   * Artifact public URLs are token-authed via the query string — pass nothing.
   * Workspace file URLs need `X-User-Id` / `Authorization`.
   */
  authHeaders?: Record<string, string>;
}

/**
 * Download a file from the backend, handling both Tauri (mixed-content /
 * cross-origin) and browser (cross-origin / auth-header) surfaces correctly.
 *
 * Does not return a value — side effects only (save to disk / trigger
 * browser save dialog). Throws on unrecoverable errors; callers should
 * wrap in try/catch if they need to surface a UI error.
 */
export async function downloadFile(opts: DownloadFileOptions): Promise<void> {
  const { url, filename, authHeaders } = opts;

  if (isTauri()) {
    // Rust path: saves to ~/Downloads, returns the absolute path.
    let savedPath: string | null;
    try {
      savedPath = await downloadFileViaTauri(url, filename, authHeaders);
    } catch (e) {
      window.alert(`Download failed: ${String(e)}`);
      return;
    }
    if (savedPath) {
      window.alert(`Saved to: ${savedPath}`);
    }
    // savedPath === null means isTauri() returned false after the initial check
    // (race condition on mount) — fall through silently; the button won't be
    // visible in that case anyway.
    return;
  }

  // Browser path: fetch with auth headers → Blob → object URL → click.
  const res = await fetch(url, {
    headers: authHeaders,
  });
  if (!res.ok) {
    throw new Error(`Download failed: HTTP ${res.status} ${res.statusText}`);
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}
