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
 * @returns the absolute path the file was saved to, on the Tauri surface only.
 *   The browser surface hands off to the download manager and has no path to
 *   report, so it returns null — as does Tauri if `isTauri()` flipped between
 *   the check and the call (a mount race; that button isn't visible then).
 *   A caller that wants to tell the user WHERE the file went must announce this
 *   value: the desktop app has no download shelf, so without it a successful
 *   save looks identical to nothing happening.
 * @throws on any failure, on BOTH surfaces. This symmetry is the point: the
 *   Tauri branch used to swallow its error and show a native alert instead,
 *   which wry does not render — so on the DMG a failed download was silent AND
 *   the caller's own `.catch()` was dead code, while the same `.catch()` worked
 *   in the browser. Reporting is the caller's job now (see useConfirm), and it
 *   is the same job on both surfaces.
 */
export async function downloadFile(opts: DownloadFileOptions): Promise<string | null> {
  const { url, filename, authHeaders } = opts;

  if (isTauri()) {
    // Rust path: saves to ~/Downloads, returns the absolute path. Errors
    // propagate — see @throws above for why this is not caught here.
    return await downloadFileViaTauri(url, filename, authHeaders);
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
  return null; // the download manager owns the file now; no path to report
}
