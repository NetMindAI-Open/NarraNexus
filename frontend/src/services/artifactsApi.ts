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

const base = (agentId: string) => `/api/agents/${agentId}/artifacts`;
const userBase = (userId: string) => `/api/users/${userId}/artifacts`;

export function authHeaders(): Record<string, string> {
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (raw) {
      const cfg = JSON.parse(raw);
      const token = cfg?.state?.token;
      if (token) return { Authorization: `Bearer ${token}` };
    }
  } catch {
    /* localStorage may be unavailable / disabled — fall through */
  }
  return {};
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

export interface ArtifactQuotaInfo {
  used_count: number;
  count_limit: number;
  used_bytes: number;
  bytes_limit: number;
  is_cloud_mode: boolean;
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
    return data.raw_url;
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

  // ── user-scoped (Settings → Artifacts management UI) ────────────────────────

  async listAll(userId: string): Promise<Artifact[]> {
    const r = await fetch(userBase(userId), { headers: authHeaders() });
    if (!r.ok) throw new Error(`listAll failed: ${r.status}`);
    return r.json();
  },

  async getQuota(userId: string): Promise<ArtifactQuotaInfo> {
    const r = await fetch(`${userBase(userId)}/quota`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`getQuota failed: ${r.status}`);
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
