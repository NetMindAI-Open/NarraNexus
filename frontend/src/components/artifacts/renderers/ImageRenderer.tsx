/**
 * @file_name: ImageRenderer.tsx
 * @description: Lazy-loaded renderer for image artifacts (image/png, image/jpeg).
 *
 * Pointer model: bytes are fetched from a token-protected public URL minted
 * via `useArtifactRawUrl`; no Authorization header is needed. The blob URL
 * is handed to <img> because native <img src=...> cannot attach headers (the
 * old reason was JWT; with token-in-URL it would now work, but the blob URL
 * approach avoids re-fetching when the same image renders in multiple cards).
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

export default function ImageRenderer({ artifact }: Props) {
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  // attempt() via ref so the load effect's deps stay `[url]` — see
  // HtmlRenderer for the bug story (Dismiss-modal loop, 2026-05-25).
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
    <div className="w-full h-full flex items-center justify-center bg-[var(--bg-deep)] p-4">
      <img
        src={src}
        alt={artifact.title}
        className="max-w-full max-h-full object-contain"
      />
    </div>
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
