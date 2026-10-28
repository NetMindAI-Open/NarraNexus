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

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import { artifactsApi, fetchArtifactBlobUrl, ArtifactEditConflictError } from '@/services/artifactsApi';
import { isTauri, fetchArtifactViaTauri } from '@/lib/tauri';
import { applyBridgeEdit, type BridgeEdit } from '@/lib/artifactEditing/htmlAnchorReplace';
import { sha256Hex } from '@/lib/sha256';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
}

function isWorkspaceRootEntry(filePath: string): boolean {
  return filePath.split('/').filter(Boolean).length <= 2;
}

/** The entry URL with the per-element edit bridge injected (spec A §3.3). */
function withEditBridge(url: string): string {
  return url + (url.includes('?') ? '&' : '?') + 'edit_bridge=1';
}

export default function HtmlRenderer({ artifact }: Props) {
  const { t } = useTranslation();
  // refreshKey — when the agent re-registers via target_artifact_id the
  // row's updated_at bumps and the iframe reloads fresh. EXCEPT when the
  // bump is the echo of OUR OWN per-element commit (the event's
  // content_hash matches what we just saved): reloading then would blank
  // the user's scroll/cursor for a document that already shows the edit.
  // Render-time state adjustment (no refs during render).
  const [seedState, setSeedState] = useState({
    seen: artifact.updated_at,
    seed: artifact.updated_at,
    selfHash: null as string | null,
  });
  if (artifact.updated_at !== seedState.seen) {
    const isSelfEcho =
      artifact.content_hash != null && artifact.content_hash === seedState.selfHash;
    setSeedState({
      seen: artifact.updated_at,
      seed: isSelfEcho ? seedState.seed : artifact.updated_at,
      selfHash: seedState.selfHash,
    });
  }
  const { url, error, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    seedState.seed,
  );
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
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
        // The blob is built from the BRIDGED entry so per-element editing
        // works on the blob path too (postMessage crosses blob origins fine).
        const bridgedUrl = withEditBridge(url);
        if (isTauri()) {
          out = await fetchArtifactViaTauri(bridgedUrl);
          if (out) source = 'tauri-ipc';
        }
        if (!out) {
          out = await fetchArtifactBlobUrl(bridgedUrl);
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

  // Per-element edit commit (spec A §3.2): fetch the CLEAN source (no
  // bridge), anchor-replace locally, PUT with the fetched bytes' hash. On a
  // 409 the source moved underneath us — retry ONCE against the fresh
  // source (the anchor often still resolves); a second failure surfaces the
  // conflict notice. Anchor failures degrade to the AI notice and never
  // write anywhere.
  const commitBridgeEdit = useCallback(
    async (edit: BridgeEdit) => {
      if (!url) return;
      setEditNotice(null);
      const attempt = async (): Promise<'ok' | 'conflict' | 'anchor-failed'> => {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
        const buf = await r.arrayBuffer();
        // fatal: a non-UTF-8 source must degrade to the AI channel, never be
        // decoded lossily and PUT back (review #334 I4 — the lock hashes the
        // fetched bytes, so it cannot catch decode corruption).
        let source: string;
        try {
          source = new TextDecoder('utf-8', { fatal: true }).decode(buf);
        } catch {
          return 'anchor-failed';
        }
        const replaced = applyBridgeEdit(source, edit);
        if (!replaced.ok) {
          return replaced.reason === 'no-change' ? 'ok' : 'anchor-failed';
        }
        try {
          const updated = await artifactsApi.putContent(artifact.agent_id, artifact.artifact_id, {
            content: replaced.result,
            base_hash: await sha256Hex(buf),
          });
          setSeedState((s) => ({ ...s, selfHash: updated.content_hash ?? null }));
          return 'ok';
        } catch (e) {
          if (e instanceof ArtifactEditConflictError) return 'conflict';
          throw e;
        }
      };
      try {
        let outcome = await attempt();
        if (outcome === 'conflict') outcome = await attempt();
        if (outcome === 'anchor-failed') {
          setEditNotice(t('artifacts.editor.htmlAnchorFailed'));
        } else if (outcome === 'conflict') {
          setEditNotice(t('artifacts.editor.htmlEditConflict'));
        }
      } catch (e) {
        console.error('html per-element edit failed', e);
        setEditNotice(t('artifacts.editor.editFailed'));
      }
    },
    [url, artifact.agent_id, artifact.artifact_id, t],
  );

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const data = e.data as { type?: string } & BridgeEdit;
      if (data?.type !== 'narra-edit-bridge:edit') return;
      // Only OUR OWN iframe may drive edits — any other window (including
      // another artifact tab's iframe) is ignored.
      const frame = iframeRef.current;
      if (!frame || e.source !== frame.contentWindow) return;
      void commitBridgeEdit(data);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [commitBridgeEdit]);

  const iframeSrc = useBlobIframe ? blobUrl : url ? withEditBridge(url) : null;

  return (
    <div className="relative w-full h-full">
      {editNotice && (
        <div className="absolute top-0 left-0 right-0 z-20 px-3 py-1.5 text-xs bg-amber-500/90 text-black flex items-center gap-2">
          <span className="flex-1">{editNotice}</span>
          <button onClick={() => setEditNotice(null)} className="px-1 font-semibold">
            ×
          </button>
        </div>
      )}
      {error || blobError ? (
        <div className="p-4 text-red-400">Failed to load: {error || blobError}</div>
      ) : !url || !iframeSrc ? (
        <div className="p-4 opacity-60">Loading…</div>
      ) : (
        // Belt-and-braces: keying the iframe on the url seed forces React to
        // remount it even if the `src` somehow doesn't change (e.g. expired
        // token re-mint that lands on the same string). The seed — not raw
        // updated_at — so our own per-element commits don't remount it.
        <iframe
          ref={iframeRef}
          key={`${seedState.seed}-${heal.recoveryVersion}`}
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
