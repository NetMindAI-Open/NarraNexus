/**
 * @file_name: PdfRenderer.tsx
 * @description: Lazy-loaded renderer for application/pdf artifacts.
 *
 * Uses <object data type="application/pdf"> rather than the HtmlRenderer
 * iframe sandbox pattern. PDF rendering is plugin-based across browsers —
 * Chromium uses PDFium, Firefox uses PDF.js (which needs same-origin XHR),
 * and WebKit/WKWebView has its own Preview-based viewer. The sandboxed iframe
 * approach (sandbox="allow-scripts" without allow-same-origin) breaks
 * Firefox's PDF.js because it requires same-origin XHR.
 *
 * Pointer model: bytes are fetched from a token-protected public URL minted
 * via `useArtifactRawUrl` and wrapped in a blob URL handed to <object>.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';

interface Props {
  artifact: Artifact;
}

export default function PdfRenderer({ artifact }: Props) {
  const { url, error: urlError } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    let createdUrl: string | null = null;
    (async () => {
      setError(null);
      try {
        const blobUrl = await fetchArtifactBlobUrl(url);
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        createdUrl = blobUrl;
        setSrc(blobUrl);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [url]);

  if (urlError) return <div className="p-4 text-red-400">Failed to load: {urlError}</div>;
  if (error) return <div className="p-4 text-red-400">Failed to load: {error}</div>;
  if (!src) return <div className="p-4 opacity-60">Loading…</div>;

  return (
    <object
      data={src}
      type="application/pdf"
      className="w-full h-full border-0"
      aria-label={artifact.title}
    >
      <div className="p-4 opacity-60">
        Your browser cannot display this PDF inline.&nbsp;
        <a href={src} target="_blank" rel="noopener noreferrer" className="underline">
          Open it in a new tab
        </a>
      </div>
    </object>
  );
}
