/**
 * @file_name: useBusAttachmentBlobUrl.ts
 * @description: Fetch a bus-message attachment (per-user shared area) as a
 *   Blob and expose a browser-local blob: URL for inline rendering / download.
 *
 * Bus counterpart to `useAttachmentBlobUrl`. Bus attachments are addressed by
 * `rel_path` and served by `GET /api/agent-inbox/attachments/raw`, which — like
 * every /api/* route — needs an Authorization/X-User-Id header that <img>/<a>
 * cannot attach. This hook does the authed GET and returns a `blob:` URL.
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export function useBusAttachmentBlobUrl(relPath: string | null | undefined): string | null {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!relPath) return;

    let cancelled = false;
    let createdUrl: string | null = null;

    api
      .fetchBusAttachmentBlob(relPath)
      .then((blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      })
      .catch(() => {
        // Silent: caller renders a placeholder/chip when the URL is null.
      });

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
      setBlobUrl(null);
    };
  }, [relPath]);

  return blobUrl;
}
