/**
 * Artifacts REST API client — thin fetch wrappers for the agent-scoped
 * (/api/agents/{agentId}/artifacts) and user-scoped
 * (/api/users/{userId}/artifacts) routes.
 *
 * Pointer model (2026-05-14): the raw content endpoint moved to
 * /api/public/artifacts/raw/{token}/{path:path} (JWT-bypassed; HMAC token in
 * the path is the auth). Frontend flow:
 *
 *   1. `getRawUrl(agentId, artifactId)` mints a token (JWT-authed) and
 *      returns the directory URL `/api/public/artifacts/raw/{token}/`.
 *   2. For HTML artifacts → set `iframe.src = rawUrl` (entry + sibling
 *      assets all served under the same path).
 *   3. For other kinds → `fetch(rawUrl)` for the entry file (no auth
 *      header needed; the token IS the auth).
 *
 * Cloud-mode auth on the JWT-authed endpoints (list/detail/patch/delete/
 * register/view-token) uses the same JWT slot the rest of the app relies on.
 */

import type { Artifact } from '@/types/artifact';
import { getApiBaseUrl } from '@/stores/runtimeStore';

// Every fetch URL is built by prepending `getApiBaseUrl()`. It returns
// `''` in cloud (page origin == backend origin, so relative paths
// resolve correctly) and `http://localhost:8000` in dmg local mode
// (where the Tauri webview origin `tauri.localhost` is NOT the backend).
// Without this prefix, dmg's artifacts panel silently 404'd on every
// list / view-token / heal call — empty list, dead clicks, no panel
// (cloud was unaffected, which masked the bug for cloud-first users).
const base = (agentId: string) => `${getApiBaseUrl()}/api/agents/${agentId}/artifacts`;
const userBase = (userId: string) => `${getApiBaseUrl()}/api/users/${userId}/artifacts`;

// Backend mints raw_url as a path like "/api/public/artifacts/raw/<tok>/".
// In dmg an iframe `src` or fetch against that path resolves against
// `tauri.localhost`, not the backend — so absolutise before returning.
// Pass-through for already-absolute URLs (defensive: future CDN-hosted
// artifact variants must not be double-prefixed).
function absolutiseBackendUrl(maybeRelative: string): string {
  if (/^https?:\/\//i.test(maybeRelative)) return maybeRelative;
  return `${getApiBaseUrl()}${maybeRelative}`;
}

export function authHeaders(): Record<string, string> {
  // Mirror the app-wide ApiClient.getAuthHeaders(): send BOTH identity headers.
  //   - Authorization: Bearer <jwt>  — trusted in cloud mode
  //   - X-User-Id: <user_id>         — required in local mode (the local
  //     auth middleware 401s any /api request that omits it)
  // The backend decides which to trust per deployment mode; cloud ignores
  // X-User-Id entirely, so sending it is safe in production. Without it,
  // artifact list/detail/view-token requests 401'd in local mode.
  const headers: Record<string, string> = {};
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (raw) {
      const cfg = JSON.parse(raw);
      const token = cfg?.state?.token;
      const userId = cfg?.state?.userId;
      if (token) headers['Authorization'] = `Bearer ${token}`;
      if (userId) headers['X-User-Id'] = userId;
    }
  } catch {
    /* localStorage may be unavailable / disabled — fall through */
  }
  return headers;
}

/**
 * Fetch raw bytes from a public artifact URL as a blob: URL.
 *
 * The URL is the directory-style `raw_url` returned by `getRawUrl()`. No
 * Authorization header is attached — the token in the URL path IS the auth.
 *
 * Caller MUST `URL.revokeObjectURL(returned)` when the blob is no longer
 * needed (typically the cleanup function of the same useEffect).
 */
export async function fetchArtifactBlobUrl(url: string): Promise<string> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  const blob = await r.blob();
  return URL.createObjectURL(blob);
}

/** Fetch raw text from a public artifact URL (csv / markdown / chart JSON). */
export async function fetchArtifactText(url: string): Promise<string> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return r.text();
}

export interface BulkDeleteResult {
  deleted: number;
  skipped_not_owned: string[];
}

export interface ViewToken {
  token: string;
  raw_url: string;
  expires_at: number;
}

export interface RegisterFromWorkspaceParams {
  file_path: string;
  kind: string;
  title: string;
  description?: string;
  target_artifact_id?: string;
}

export interface HealCandidate {
  workspace_path: string;
  size_bytes: number;
  mtime: number;
}

export interface HealResponse {
  recovered: boolean;
  artifact: Artifact | null;
  candidates: HealCandidate[];
  message: string;
}

export const artifactsApi = {
  async listSession(agentId: string, sessionId: string): Promise<Artifact[]> {
    const url = `${base(agentId)}?scope=session&session_id=${encodeURIComponent(sessionId)}`;
    const r = await fetch(url, { headers: authHeaders() });
    if (!r.ok) throw new Error(`listSession failed: ${r.status}`);
    return r.json();
  },

  async listPinned(agentId: string): Promise<Artifact[]> {
    const r = await fetch(`${base(agentId)}?scope=pinned`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`listPinned failed: ${r.status}`);
    return r.json();
  },

  async getDetail(agentId: string, artifactId: string): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`getDetail failed: ${r.status}`);
    return r.json();
  },

  /**
   * Mint a short-TTL view token and return the directory URL for raw content.
   *
   * The returned URL ends in `/` so a relative `./style.css` inside an entry
   * HTML resolves to a sibling asset under the same token-protected path.
   */
  async getRawUrl(agentId: string, artifactId: string): Promise<string> {
    const r = await fetch(`${base(agentId)}/${artifactId}/view-token`, {
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`view-token failed: ${r.status}`);
    const data: ViewToken = await r.json();
    return absolutiseBackendUrl(data.raw_url);
  },

  async setPinned(agentId: string, artifactId: string, pinned: boolean): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ pinned }),
    });
    if (!r.ok) throw new Error(`setPinned failed: ${r.status}`);
    return r.json();
  },

  /**
   * Try to recover an artifact whose `/raw/` returned 410 (file_path is
   * null in the DB or the file is missing on disk).
   *
   * Omit `entryPath` → server runs the workspace-scan heuristic and either
   * auto-registers (single match) or returns a list of candidates the
   * user can pick from. Pass `entryPath` → server re-registers onto the
   * caller-chosen path (the "user picked from the modal" flow).
   */
  async heal(
    agentId: string,
    artifactId: string,
    entryPath?: string,
  ): Promise<HealResponse> {
    const r = await fetch(`${base(agentId)}/${artifactId}/heal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ entry_path: entryPath ?? null }),
    });
    if (!r.ok) throw new Error(`heal failed: ${r.status}`);
    return r.json();
  },

  /**
   * Delete an artifact's registry row. The agent's workspace files are NEVER
   * touched — the user cleans those up via the workspace section if they
   * want. This pointer-only deletion replaced the previous `delete_source`
   * option together with the "no workspace-root entry" hard rule in r3.
   */
  async remove(agentId: string, artifactId: string): Promise<void> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`delete failed: ${r.status}`);
  },

  /**
   * Register a workspace file as an artifact. Used by the workspace tree
   * viewer's "register as artifact" action. Delegates to the same runner the
   * MCP `register_artifact` tool uses, so validation rules are identical
   * (path must live in a workspace subdirectory, kind whitelist, quota, ...).
   */
  async registerFromWorkspace(
    agentId: string,
    params: RegisterFromWorkspaceParams,
  ): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(params),
    });
    if (!r.ok) {
      let detail: string = String(r.status);
      try {
        const body = await r.json();
        if (body?.detail) detail = String(body.detail);
      } catch { /* not JSON */ }
      throw new Error(detail);
    }
    return r.json();
  },

  /**
   * Open a web page as a URL-tab artifact. The backend probes the URL's
   * embeddability and stores the verdict; the initial URL is SSRF-gated
   * (a non-public target is rejected with the backend's error detail).
   */
  async openUrl(agentId: string, url: string, title?: string): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ url, title }),
    });
    if (!r.ok) {
      let detail: string = String(r.status);
      try {
        const body = await r.json();
        if (body?.detail) detail = String(body.detail);
      } catch { /* not JSON */ }
      throw new Error(detail);
    }
    return r.json();
  },

  /**
   * Set (or clear, mode=null) the user's manual embed override on a URL tab.
   * The override wins over the probe recommendation for that tab.
   */
  async setEmbedMode(
    agentId: string,
    artifactId: string,
    mode: 'iframe' | 'stream' | null,
  ): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/${artifactId}/embed-mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ mode }),
    });
    if (!r.ok) throw new Error(`setEmbedMode failed: ${r.status}`);
    return r.json();
  },

  // ── user-scoped (Settings → Artifacts management UI) ────────────────────────

  async listAll(userId: string): Promise<Artifact[]> {
    const r = await fetch(userBase(userId), { headers: authHeaders() });
    if (!r.ok) throw new Error(`listAll failed: ${r.status}`);
    return r.json();
  },

  async bulkDelete(userId: string, artifactIds: string[]): Promise<BulkDeleteResult> {
    const r = await fetch(userBase(userId), {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ artifact_ids: artifactIds }),
    });
    if (!r.ok) throw new Error(`bulkDelete failed: ${r.status}`);
    return r.json();
  },
};
