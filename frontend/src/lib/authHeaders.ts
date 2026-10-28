/**
 * @file_name: authHeaders.ts
 * @description: Identity headers for outbound requests, read straight from
 * localStorage.
 *
 * Extracted from `lib/api.ts` so the session guard can probe
 * `GET /api/auth/session` without importing the API client (which imports
 * the guard — a cycle). Reading localStorage rather than `configStore` is
 * the same deliberate constraint api.ts has always lived under: importing
 * the store here would create `api → configStore → api`.
 */

const CONFIG_KEY = 'narra-nexus-config';

interface PersistedIdentity {
  token: string;
  userId: string;
}

/**
 * The two identity fields out of the persisted configStore blob.
 *
 * Single parse point on purpose: both exports below need the same blob, and
 * duplicating `getItem` + `JSON.parse` + try/catch means duplicating the
 * failure handling too. localStorage can be unavailable outright (private
 * mode, storage disabled) and the blob can be malformed (a half-written
 * value, a schema from an older build) — either way the honest answer is
 * "no identity", which the backend then rejects with `token_missing` /
 * `identity_missing`. That is correct behaviour, not a bug to paper over.
 */
function readIdentity(): PersistedIdentity {
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    if (!raw) return { token: '', userId: '' };
    const state = JSON.parse(raw)?.state;
    return {
      token: typeof state?.token === 'string' ? state.token : '',
      userId: typeof state?.userId === 'string' ? state.userId : '',
    };
  } catch {
    return { token: '', userId: '' };
  }
}

/**
 * Two headers, mutually compatible:
 *   - `Authorization: Bearer <jwt>` — cloud mode, signed identity
 *   - `X-User-Id: <user_id>`        — local mode, unsigned identity
 *
 * Both are sent whenever available; the backend's auth_middleware decides
 * which one to trust (cloud: JWT only, X-User-Id ignored as defence in
 * depth; local: X-User-Id only, there is no signing key).
 */
export function getAuthHeaders(): Record<string, string> {
  const { token, userId } = readIdentity();
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (userId) headers['X-User-Id'] = userId;
  return headers;
}

/** The raw session JWT, or '' when there is none (local mode / logged out). */
export function getSessionToken(): string {
  return readIdentity().token;
}
