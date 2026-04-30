---
code_file: frontend/src/hooks/useAttachmentBlobUrl.ts
last_verified: 2026-04-29
stub: false
---

# useAttachmentBlobUrl.ts — Authed binary fetch → blob: URL

## Why it exists

Cloud-mode auth requires `Authorization: Bearer <token>` on every `/api/*` request (see `backend/auth.py:auth_middleware`). HTML `<img>` / `<a>` / `<embed>` elements have no API to attach custom headers — that's a browser-spec limitation, not a project gap. So a naive `<img src="/api/.../attachments/.../raw">` always 401s with `{"detail":"Authentication required"}` in cloud mode.

This hook is the workaround: do the GET via `fetch()` (which can carry the Authorization header), wrap the bytes in `URL.createObjectURL`, and return a session-scoped `blob:` URL that any HTML element can consume without further auth.

## Upstream / Downstream

- **Used by**: `AttachmentImage` component (only caller). The component decouples hook lifetime from render order so callers can use it inside `.map()` without violating the rules of hooks.
- **Calls**: `api.fetchAttachmentBlob(agentId, userId, fileId)` — the only path through `lib/api.ts` that returns a `Blob` instead of JSON.

## Design decisions

**Same code path for local and cloud mode.** Local mode has no token in `localStorage`, so `getAuthHeaders()` returns `{}`, and the backend bypass (`_is_cloud_mode()=False`) lets the request through. Cloud mode injects the Bearer token. No `if (cloudMode)` branching is needed in this hook.

**Silent on fetch error.** A failed thumbnail must not spam the console for every render and must not block the agent run flow (the agent reads the file directly from the workspace path, not via this URL). The hook returns `null` on failure and the caller renders a placeholder.

**Cleanup revokes the blob URL and clears state.** `setBlobUrl(null)` lives only in cleanup, not in the effect body, so React lint's "no synchronous setState in effect" rule is satisfied. Cleanup runs both on unmount and when `agentId/userId/fileId` change.

**`cancelled` flag prevents stale state writes.** If deps change while the previous fetch is still in flight, the resolved blob is dropped and its URL is never created (and so doesn't need revoking).

## Gotchas

**Blob URL is alive only as long as the consuming component is mounted.** If a parent unmounts (e.g., user navigates away from chat), the URL is revoked immediately. A separate tab opened via `<a href={blobUrl} target="_blank">` would 404 the blob after that point. Acceptable for thumbnail click-to-zoom; not acceptable for a "share this link" flow.

**Memory pressure scales with concurrent thumbnails.** Each blob is held in JS heap until revoked. For chat history with hundreds of image attachments rendered at once, this could noticeably balloon memory. Current chat history pagination keeps the count low; revisit if a "load all history" button is added.

**No client-side cache across renders or routes.** Switching between two agents that both have image attachments will refetch each blob. If thumbnail traffic becomes hot, layer a `Map<file_id, Blob>` cache in `lib/api.ts` or a stores/blobCacheStore.
