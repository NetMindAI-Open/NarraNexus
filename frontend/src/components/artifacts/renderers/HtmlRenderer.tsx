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

import { useEffect } from 'react';
import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
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
          heal.attempt();
        }
      } catch {
        /* network blip — the iframe will surface its own error */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url, heal]);

  return (
    <>
      {error ? (
        <div className="p-4 text-red-400">Failed to load: {error}</div>
      ) : !url ? (
        <div className="p-4 opacity-60">Loading…</div>
      ) : (
        // Belt-and-braces: keying the iframe on updated_at forces React to
        // remount it even if the `src` somehow doesn't change (e.g. expired
        // token re-mint that lands on the same string).
        <iframe
          key={`${artifact.updated_at}-${heal.recoveryVersion}`}
          title={artifact.title}
          sandbox="allow-scripts"
          src={url}
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
    </>
  );
}
