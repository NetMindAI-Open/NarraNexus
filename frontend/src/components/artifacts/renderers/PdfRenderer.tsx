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

import { useEffect, useRef, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
}

export default function PdfRenderer({ artifact }: Props) {
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  // attempt() via ref so the load effect's deps stay `[url]` — pulling
  // `heal` in re-fired the blob fetch on every hook state change and
  // bounced the modal back open after Dismiss. Bug: 2026-05-25.
  const attemptRef = useRef(heal.attempt);
  useEffect(() => {
    attemptRef.current = heal.attempt;
  }, [heal.attempt]);

  useEffect(() => {
    if (heal.recoveryVersion > 0) reload();
  }, [heal.recoveryVersion, reload]);

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
        if (cancelled) return;
        const msg = String(e);
        setError(msg);
        if (msg.includes('fetch failed: 410')) attemptRef.current();
      }
    })();
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [url]);

  const content = urlError ? (
    <div className="p-4 text-red-400">Failed to load: {urlError}</div>
  ) : error ? (
    <div className="p-4 text-red-400">Failed to load: {error}</div>
  ) : !src ? (
    <div className="p-4 opacity-60">Loading…</div>
  ) : (
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

  return (
    <>
      {content}
      <ArtifactHealModal
        open={heal.modalOpen}
        artifactTitle={artifact.title}
        candidates={heal.candidates}
        message={heal.message}
        busy={heal.busy}
        onPick={(workspacePath) => heal.attempt(workspacePath)}
        onDismiss={heal.dismiss}
      />
    </>
  );
}
