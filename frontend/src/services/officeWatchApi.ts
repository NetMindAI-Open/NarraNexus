/**
 * @file_name: officeWatchApi.ts
 * @description: Mint the signed iframe URL for the live Office-watch preview.
 * The mint call is session-authed (authHeaders injects X-User-Id / JWT); the
 * returned URL embeds a token so the <iframe> navigation + the watch page's
 * own EventSource/fetch sub-requests carry auth in the path (no header). Same
 * shape as artifactsApi.getRawUrl.
 */

import { getApiBaseUrl } from '@/stores/runtimeStore';
import { authHeaders } from './artifactsApi';

export const officeWatchApi = {
  /** Open (or re-open) the live preview for an office artifact.
   *
   * Artifact-anchored + on-demand: the backend ensures a watch is running for
   * the artifact's file (restarting it if it died / idle-stopped) and returns a
   * token-signed iframe URL. This is why refresh / reopen works — we never rely
   * on a remembered, possibly-dead port. Returns the absolute iframe URL.
   */
  async open(artifactId: string): Promise<string> {
    const q = `artifact_id=${encodeURIComponent(artifactId)}`;
    const r = await fetch(`${getApiBaseUrl()}/api/office-watch/open?${q}`, {
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`office-watch open failed: ${r.status}`);
    const data = (await r.json()) as { raw_url: string };
    // Absolutise so the iframe src resolves against the backend, not the SPA
    // origin (matters in dmg where the origin is tauri.localhost).
    return `${getApiBaseUrl()}${data.raw_url}`;
  },

  /** Poll the office file's change-signal (mtime + size).
   *
   * The viewer uses this as the correctness FALLBACK behind the smooth SSE
   * path: when mtime advances but no content SSE frame arrived (officecli's
   * resident wasn't shared, so the watch never live-refreshed), it reloads the
   * iframe — the watch page's own GET always renders the current document.
   */
  async version(artifactId: string): Promise<{ mtime: number; size: number }> {
    const q = `artifact_id=${encodeURIComponent(artifactId)}`;
    const r = await fetch(`${getApiBaseUrl()}/api/office-watch/version?${q}`, {
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`office-watch version failed: ${r.status}`);
    return (await r.json()) as { mtime: number; size: number };
  },
};
