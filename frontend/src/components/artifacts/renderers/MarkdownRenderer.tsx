/**
 * @file_name: MarkdownRenderer.tsx
 * @description: Lazy-loaded renderer for text/markdown artifacts.
 *
 * Fetches raw Markdown text and renders it via ReactMarkdown + remark-gfm.
 * Uses the same vendor-markdown chunk that MessageBubble / ui/Markdown.tsx
 * already relies on, so no additional bundle cost when Markdown is used
 * elsewhere in the session.
 *
 * Pointer model: the entry file is served from a token-protected directory
 * URL minted via `useArtifactRawUrl`. No auth header is needed on the fetch.
 */

import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Artifact } from '@/types/artifact';
import { fetchArtifactText } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
}

export default function MarkdownRenderer({ artifact }: Props) {
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [text, setText] = useState<string>('');
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
    (async () => {
      setError(null);
      try {
        const t = await fetchArtifactText(url);
        if (!cancelled) setText(t);
      } catch (e) {
        if (cancelled) return;
        const msg = String(e);
        setError(msg);
        if (msg.includes('fetch failed: 410')) attemptRef.current();
      }
    })();
    return () => { cancelled = true; };
  }, [url]);

  const content = urlError ? (
    <div className="p-4 text-red-400">Failed to load: {urlError}</div>
  ) : error ? (
    <div className="p-4 text-red-400">Failed to load: {error}</div>
  ) : !url ? (
    <div className="p-4 opacity-60">Loading…</div>
  ) : !text ? (
    <div className="p-4 opacity-60">(empty markdown)</div>
  ) : (
    <div className="markdown-content max-w-none p-4 overflow-auto">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
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
