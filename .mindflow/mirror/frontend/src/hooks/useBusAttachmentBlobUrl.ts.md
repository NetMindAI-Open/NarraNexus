---
code_file: frontend/src/hooks/useBusAttachmentBlobUrl.ts
last_verified: 2026-07-20
stub: false
---

# useBusAttachmentBlobUrl.ts — authed blob URL for a bus attachment

## Why it exists

Bus counterpart to [[useAttachmentBlobUrl]]. Every `/api/*` route needs an
`Authorization: Bearer` / `X-User-Id` header, which `<img src>` / `<a href>`
cannot attach — so a naive `<img src="/api/.../raw">` 401s in cloud mode. This
hook does the authed GET via `api.fetchBusAttachmentBlob(relPath)`, wraps the
bytes in `URL.createObjectURL`, and returns a session-scoped `blob:` URL.

## Difference from useAttachmentBlobUrl

That hook is keyed by `(agentId, userId, fileId)` and hits the agent-scoped
attachment endpoint. This one is keyed by a single `rel_path` and hits the
shared-area bus endpoint `GET /api/agent-inbox/attachments/raw?path=…`. Cleanup
revokes the object URL on unmount / dep change. Used by [[BusAttachmentList]].
