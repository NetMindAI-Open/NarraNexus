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
  async version(
    artifactId: string,
  ): Promise<{ mtime: number; size: number; lock?: boolean }> {
    const q = `artifact_id=${encodeURIComponent(artifactId)}`;
    const r = await fetch(`${getApiBaseUrl()}/api/office-watch/version?${q}`, {
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`office-watch version failed: ${r.status}`);
    return (await r.json()) as { mtime: number; size: number; lock?: boolean };
  },

  /**
   * Run edit commands via the SESSION-AUTHED backend endpoint (review #334
   * I14): the view token stays a pure read credential, and the same-origin
   * hop sidesteps the desktop WKWebView mixed-content block. The backend
   * ensures the watch and forwards to its /api/batch.
   */
  async sendBatch(artifactId: string, commands: unknown[]): Promise<{ ok: boolean; raw: unknown }> {
    const q = `artifact_id=${encodeURIComponent(artifactId)}`;
    const r = await fetch(`${getApiBaseUrl()}/api/office-watch/edit?${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(commands),
    });
    if (!r.ok) throw new Error(`watch batch failed: ${r.status}`);
    const outer = (await r.json()) as { success?: boolean; message?: string };
    // The watch server double-wraps: outer {success, message} where message
    // is the JSON of the real batch result.
    try {
      const inner = JSON.parse(outer.message ?? '{}');
      return { ok: Boolean(inner?.success), raw: inner };
    } catch {
      return { ok: Boolean(outer?.success), raw: outer };
    }
  },

  /** Read one element (path → {text, ...}) for pre-filling the text editor. */
  async getElement(artifactId: string, path: string): Promise<{ text?: string } | null> {
    try {
      const res = await officeWatchApi.sendBatch(artifactId, [{ command: 'get', path }]);
      const inner = res.raw as { data?: { results?: Array<{ output?: { results?: Array<{ text?: string }> } }> } };
      return inner?.data?.results?.[0]?.output?.results?.[0] ?? null;
    } catch {
      return null;
    }
  },

  /** Land an image next to the office entry for a T2 replace; returns the
   * absolute server path `set … src=` resolves against. */
  async uploadAsset(agentId: string, artifactId: string, file: File): Promise<string> {
    const form = new FormData();
    form.append('file', file);
    const r = await fetch(
      `${getApiBaseUrl()}/api/agents/${agentId}/artifacts/${artifactId}/office-asset`,
      { method: 'POST', headers: authHeaders(), body: form },
    );
    if (!r.ok) throw new Error(`office-asset upload failed: ${r.status}`);
    return ((await r.json()) as { path: string }).path;
  },

  /** Turn an applied watch edit into a registry commit point (spec B §3.2). */
  async commitEdit(agentId: string, artifactId: string): Promise<void> {
    const r = await fetch(
      `${getApiBaseUrl()}/api/agents/${agentId}/artifacts/${artifactId}/office-edit-commit`,
      { method: 'POST', headers: authHeaders() },
    );
    if (!r.ok) throw new Error(`office-edit-commit failed: ${r.status}`);
  },
};
