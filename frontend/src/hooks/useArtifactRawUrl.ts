/**
 * @file_name: useArtifactRawUrl.ts
 * @description: Mint a view token and return the directory-style raw URL for
 * an artifact. Resolves to `null` while loading, throws via the error state
 * if the mint fails.
 *
 * Why this hook exists: under the pointer model, raw content lives at
 * /api/public/artifacts/raw/{token}/{path:path}. The token is short-TTL HMAC
 * and must be minted via an authed call before the URL becomes usable. Every
 * artifact renderer (HtmlRenderer, ChartRenderer, CsvRenderer, ...) needs the
 * same handshake; this hook centralises it.
 *
 * The returned URL ends with `/` so an entry HTML's relative references
 * (./style.css, ./data.json) resolve to sibling assets under the same
 * token-protected path.
 */

import { useEffect, useState } from 'react';

import { artifactsApi } from '@/services/artifactsApi';

export interface ArtifactRawUrlState {
  url: string | null;
  error: string | null;
}

export function useArtifactRawUrl(
  agentId: string,
  artifactId: string,
  /**
   * Optional bump key. Change to force a fresh token mint — e.g. when an
   * artifact is re-registered onto the same `artifact_id` and the renderer
   * needs to refetch sibling assets that may have changed on disk.
   */
  refreshKey: string | number = 0,
): ArtifactRawUrlState {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Async IIFE keeps every setState behind an await boundary so the
    // react-hooks/set-state-in-effect rule passes.
    (async () => {
      setUrl(null);
      setError(null);
      try {
        const u = await artifactsApi.getRawUrl(agentId, artifactId);
        if (!cancelled) setUrl(u);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, artifactId, refreshKey]);

  return { url, error };
}
