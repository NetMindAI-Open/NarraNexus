/**
 * @file_name: useAttachmentBlobUrl.ts
 * @description: Fetch a JWT-protected attachment as a Blob and expose a
 *   browser-local blob: URL for inline rendering.
 *
 * Why this exists: cloud-mode auth requires `Authorization: Bearer <token>`
 * on every /api/* request. HTML <img>/<a> elements cannot attach custom
 * headers, so a naive `<img src="/api/.../raw">` always 401s in cloud
 * mode. This hook does the authed GET via fetch(), wraps the bytes in
 * `URL.createObjectURL`, and returns a session-scoped blob URL that any
 * <img> / <a> / <embed> can consume without further auth.
 *
 * Local mode: no token in localStorage → empty Authorization header →
 * backend `_is_cloud_mode()=False` bypass passes through. Same code path
 * works for both modes.
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export function useAttachmentBlobUrl(
  agentId: string | null | undefined,
  userId: string | null | undefined,
  fileId: string | null | undefined,
): string | null {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId || !userId || !fileId) {
      // No inputs: ensure any blob URL from a previous deps cycle is gone.
      // Cleanup of the previous effect already cleared it; this branch is
      // a no-op on first render (blobUrl starts null) and is required only
      // so we don't fall through into the fetch with empty IDs.
      return;
    }

    let cancelled = false;
    let createdUrl: string | null = null;

    api
      .fetchAttachmentBlob(agentId, userId, fileId)
      .then((blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      })
      .catch(() => {
        // Silent: caller renders a placeholder when the URL is null.
        // Logging here would spam the console for every failed thumbnail
        // and the agent run flow doesn't depend on the preview rendering.
      });

    return () => {
      cancelled = true;
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl);
      }
      // setState during cleanup runs before the next render, so this does
      // not trigger a cascading render in the same commit.
      setBlobUrl(null);
    };
  }, [agentId, userId, fileId]);

  return blobUrl;
}
