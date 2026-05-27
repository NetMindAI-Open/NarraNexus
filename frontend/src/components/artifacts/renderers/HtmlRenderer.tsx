/**
 * @file_name: HtmlRenderer.tsx
 * @description: Security-hardened renderer for text/html artifacts.
 *
 * Renders agent-emitted HTML inside an isolated iframe. Supports
 * multi-file artifacts: the entry HTML may reference sibling assets in its
 * own folder (./style.css, ./app.js, ./data.json, images) — they are served
 * from the same token-protected directory URL the iframe `src` points at.
 *
 * Security contract (must NOT be relaxed without a spec change):
 *   sandbox = "allow-scripts"        — inline JS may run
 *   ✗ allow-same-origin              — iframe is opaque-origin; cannot read parent DOM or cookies
 *   ✗ allow-top-navigation           — cannot break out / redirect the top frame
 *   ✗ allow-popups-to-escape-sandbox — cannot spawn an unsandboxed window
 *   referrerPolicy = no-referrer     — no origin leak to any external destination
 *
 * Why allow-scripts but no allow-same-origin?
 *   With allow-same-origin the iframe shares the parent origin and can read
 *   parent localStorage, cookies, and DOM — a trivial XSS escape. Without it,
 *   the iframe is opaque-origin and isolated. allow-scripts is needed for
 *   chart libraries and interactive demos.
 *
 * Why iframe `src` (not blob: URL)?
 *   blob: URLs break relative sub-resource resolution (the entry HTML's
 *   `./style.css` would not resolve). The pointer model needs sibling assets
 *   to work, so the iframe loads a real URL. Authentication uses the HMAC
 *   token embedded in the URL path — see `_artifact_token.py` and
 *   `artifacts_public.py` on the backend.
 *
 *   The CSP header on the entry response (built dynamically from the request
 *   origin) restricts sub-resource loading to the same host — external
 *   destinations stay blocked. Combined with the opaque-origin sandbox, this
 *   gives the same isolation guarantees as the previous blob: design while
 *   making multi-file artifacts actually work.
 */

import { useEffect, useRef, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';
import { isTauri, fetchArtifactViaTauri } from '@/lib/tauri';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
}

function isWorkspaceRootEntry(filePath: string): boolean {
  return filePath.split('/').filter(Boolean).length <= 2;
}

export default function HtmlRenderer({ artifact }: Props) {
  // refreshKey = updated_at — when the agent re-registers via
  // target_artifact_id, the row's updated_at bumps, our store upserts the
  // new row, this hook re-mints a token, and the iframe `src` changes so
  // the document and its sibling assets reload fresh.
  const { url, error, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [blobError, setBlobError] = useState<string | null>(null);
  const [blobSource, setBlobSource] = useState<'tauri-ipc' | 'http-fetch' | null>(null);
  // In Tauri the parent webview is `https://tauri.localhost` and the backend
  // serves `http://localhost:8000` — WKWebView treats that as active mixed
  // content and silently kills the iframe load. The blob: path (which makes
  // the iframe same-origin to the parent) sidesteps the block. So in Tauri
  // we use blob: for ALL HTML, not just workspace-root single-file. The
  // tradeoff: a multi-file artifact's sibling `./style.css` will not resolve
  // off a blob URL (no base href), but the entry HTML at least renders —
  // strictly better than the white screen P0 (2026-05-27). Cloud / browser
  // stays on the original logic (workspace-root → blob, subfolder → raw URL
  // iframe so sibling assets resolve).
  const useBlobIframe = isWorkspaceRootEntry(artifact.file_path) || isTauri();
  // Stash the latest attempt() in a ref so the HEAD-probe effect only
  // needs `url` in its deps. Without this the effect re-ran on every
  // hook state change (the controller object changed identity for any
  // setModalOpen / setBusy / setMessage call), creating an HEAD→attempt
  // loop that the user couldn't escape via Dismiss. Bug: 2026-05-25.
  const attemptRef = useRef(heal.attempt);
  useEffect(() => {
    attemptRef.current = heal.attempt;
  }, [heal.attempt]);

  // Heal-success path: hook bumped recoveryVersion → re-mint the URL so
  // the iframe key changes and reloads the now-valid pointer.
  useEffect(() => {
    if (heal.recoveryVersion > 0) reload();
  }, [heal.recoveryVersion, reload]);

  // iframe.src swallows HTTP status from JS land, so we probe the URL with
  // a HEAD before letting the iframe load. 410 = broken pointer (file_path
  // NULL or off-disk) — kick off the self-heal flow instead of leaving the
  // user with a blank frame and no recourse.
  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(url, { method: 'HEAD' });
        if (!cancelled && r.status === 410) {
          attemptRef.current();
        }
      } catch {
        /* network blip — the iframe will surface its own error */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    if (!url || !useBlobIframe) {
      setBlobUrl(null);
      setBlobError(null);
      setBlobSource(null);
      return;
    }

    let cancelled = false;
    let nextBlobUrl: string | null = null;
    setBlobUrl(null);
    setBlobError(null);
    setBlobSource(null);
    (async () => {
      try {
        // In Tauri prefer the IPC path: Rust uses reqwest which is not
        // subject to WKWebView's mixed-content block (`https://tauri.localhost`
        // parent → `http://localhost:8000` artifact bytes would otherwise be
        // killed silently by the webview — the 2026-05-27 white-screen P0).
        // Fall back to plain `fetch()` if IPC returns null (browser mode, or
        // any future IPC regression).
        let source: 'tauri-ipc' | 'http-fetch' | null = null;
        let out: string | null = null;
        if (isTauri()) {
          out = await fetchArtifactViaTauri(url);
          if (out) source = 'tauri-ipc';
        }
        if (!out) {
          out = await fetchArtifactBlobUrl(url);
          if (out) source = 'http-fetch';
        }
        if (!cancelled && out) {
          nextBlobUrl = out;
          setBlobUrl(out);
          setBlobSource(source);
        }
      } catch (e) {
        if (!cancelled) setBlobError(String(e));
      }
    })();

    return () => {
      cancelled = true;
      if (nextBlobUrl) URL.revokeObjectURL(nextBlobUrl);
    };
  }, [url, useBlobIframe]);

  const iframeSrc = useBlobIframe ? blobUrl : url;

  return (
    <div className="relative w-full h-full">
      {error || blobError ? (
        <div className="p-4 text-red-400">Failed to load: {error || blobError}</div>
      ) : !url || !iframeSrc ? (
        <div className="p-4 opacity-60">Loading…</div>
      ) : (
        // Belt-and-braces: keying the iframe on updated_at forces React to
        // remount it even if the `src` somehow doesn't change (e.g. expired
        // token re-mint that lands on the same string).
        <iframe
          key={`${artifact.updated_at}-${heal.recoveryVersion}`}
          title={artifact.title}
          sandbox="allow-scripts"
          src={iframeSrc}
          referrerPolicy="no-referrer"
          loading="lazy"
          className="w-full h-full border-0 bg-white"
        />
      )}
      <ArtifactHealModal
        open={heal.modalOpen}
        artifactTitle={artifact.title}
        candidates={heal.candidates}
        message={heal.message}
        busy={heal.busy}
        onPick={(workspacePath) => heal.attempt(workspacePath)}
        onDismiss={heal.dismiss}
      />
      {/* Diagnostic overlay — folded by default, always present so we can
          eyeball renderer state in environments without devtools. Tauri
          WKWebView ships without Safari Web Inspector unless the `devtools`
          Cargo feature is enabled AND the user finds it; this overlay is
          the fallback. Tiny + low opacity so it stays out of the way. */}
      <details className="absolute bottom-1 right-1 z-10 text-[10px] font-mono bg-white/90 text-gray-700 px-1 rounded shadow-sm opacity-60 hover:opacity-100 max-w-[420px]">
        <summary className="cursor-pointer select-none">
          {useBlobIframe ? 'blob' : 'raw'}·{blobSource ?? (useBlobIframe ? '…' : 'iframe-src')}·{isTauri() ? 'tauri' : 'web'}{(error || blobError) ? '·err' : ''}
        </summary>
        <div className="p-1 space-y-0.5">
          <div>mode: {useBlobIframe ? 'blob iframe' : 'raw URL iframe'}</div>
          <div>tauri: {String(isTauri())}</div>
          <div>blobSource: {blobSource ?? '(none)'}</div>
          <div className="break-all">url: {url ?? '(none)'}</div>
          <div className="break-all">iframeSrc: {iframeSrc ?? '(none)'}</div>
          <div>blobError: {blobError ?? '(none)'}</div>
          <div>urlError: {error ?? '(none)'}</div>
          <div>file_path: {artifact.file_path}</div>
          <div>kind: {artifact.kind}</div>
        </div>
      </details>
    </div>
  );
}
