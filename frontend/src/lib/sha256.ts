/**
 * @file_name: sha256.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: sha256 hex digest over UTF-8 bytes — the client half of the
 * user-edit optimistic lock. Must produce exactly what the backend's
 * hashlib.sha256 computes over the same file bytes: the editor hashes the
 * bytes it LOADED (not the table's possibly-stale fingerprint) and sends that
 * as base_hash with every save.
 */

export async function sha256Hex(data: string | ArrayBuffer): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : new Uint8Array(data);
  const digest = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}
