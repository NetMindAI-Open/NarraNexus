/**
 * @file_name: UrlRenderer.tsx
 * @description: Renderer for URL-tab artifacts (application/x-url).
 *
 * The artifact's entry file is a small JSON doc (UrlArtifactDoc) holding the
 * URL + the server-side embed verdict. This renderer:
 *   - fetches the doc through the token-authed raw route,
 *   - iframes the URL when it is embeddable (effective_mode = 'iframe'),
 *   - otherwise shows a fallback card (open-in-new-window) — this is where the
 *     future streaming renderer (the streaming-browser plan) plugs in for effective_mode = 'stream',
 *   - always offers a mode toggle so the user can override a wrong verdict;
 *     the override is persisted via the embed-mode endpoint.
 *
 * The iframe points at the EXTERNAL url directly (not our backend), so the
 * Tauri mixed-content dodge used by HtmlRenderer is unnecessary for https
 * targets — the same code path serves both run modes.
 *
 * SECURITY — the iframe sandbox includes `allow-same-origin` (unlike
 * HtmlRenderer, which forbids it). This is safe only for CROSS-origin
 * third-party content: allow-same-origin keeps the iframe on the TARGET
 * site's origin, which (being cross-origin) cannot reach our parent DOM/token.
 * A URL tab pointing at OUR OWN origin would instead become a same-origin
 * scriptable iframe able to read the app token — so the backend
 * (`url_artifact._reject_self_origin`, browser-accurate origin comparison +
 * the request-derived app origin) refuses to open such a tab. The safety of
 * this sandbox depends on that guard; do NOT copy this sandbox to a renderer
 * that can load first-party (same-origin) content.
 *
 * NAVIGATION — the hard iframe reality: a cross-origin page's link clicks are
 * invisible to us (same-origin policy), so we can NEITHER read the target URL
 * NOR redirect it into a new in-app tab. A `target="_blank"` link therefore has
 * only two possible fates: open a new OS-browser tab, or be blocked entirely.
 * We keep `allow-popups allow-popups-to-escape-sandbox` so those links WORK
 * (open in the browser) — a link that does nothing is worse than one that
 * opens externally. Same-frame links (no target) navigate inside the tab as
 * expected. True "open every link as an in-app tab" is a streaming-browser
 * capability (we'd control the browser), not something an iframe can do.
 *
 * RUN-MODE (铁律 #7) — the above holds in BROWSER mode (bash run.sh / cloud).
 * On the packaged DESKTOP app (Tauri/WKWebView) `window.open` / target=_blank
 * popups are blocked by the webview (see
 * tauri/src-tauri/src/commands/netmind_oauth.rs — the OAuth flow had to move to
 * a Rust child-webview for exactly this reason), so these links likely remain
 * dead on the DMG until a Tauri-side new-window handler routes them to the OS
 * browser (shell open), or the streaming browser lands. Tracked as a follow-up;
 * not fixable from the frontend alone.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, RefreshCw, MonitorPlay, SquareArrowOutUpRight } from 'lucide-react';

import type { Artifact, UrlArtifactDoc } from '@/types/artifact';
import { effectiveEmbedMode } from '@/types/artifact';
import { artifactsApi, fetchArtifactText } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';

interface Props {
  artifact: Artifact;
}

export default function UrlRenderer({ artifact }: Props) {
  const { t } = useTranslation();
  const [refreshKey, setRefreshKey] = useState(0);
  const { url: rawUrl, error: rawError } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    refreshKey,
  );

  const [doc, setDoc] = useState<UrlArtifactDoc | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!rawUrl) return;
    let cancelled = false;
    (async () => {
      setDocError(null);
      try {
        const text = await fetchArtifactText(rawUrl);
        if (!cancelled) setDoc(JSON.parse(text) as UrlArtifactDoc);
      } catch (e) {
        if (!cancelled) setDocError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [rawUrl]);

  const setMode = async (mode: 'iframe' | 'stream' | null) => {
    setBusy(true);
    try {
      await artifactsApi.setEmbedMode(artifact.agent_id, artifact.artifact_id, mode);
      setRefreshKey((k) => k + 1); // re-mint token + refetch the doc
    } catch (e) {
      window.alert(t('artifacts.url.toggleFailed', 'Could not change mode: {{error}}', { error: String(e) }));
    } finally {
      setBusy(false);
    }
  };

  if (rawError || docError) {
    return (
      <div className="p-4 text-sm opacity-70">
        {t('artifacts.url.loadFailed', 'Could not load this URL tab.')} {rawError ?? docError}
      </div>
    );
  }
  if (!doc) {
    return <div className="p-4 text-sm opacity-60">{t('artifacts.url.loading', 'Loading…')}</div>;
  }

  const mode = effectiveEmbedMode(doc.embed);
  const host = safeHost(doc.url);

  return (
    <div className="flex flex-col h-full w-full">
      <ModeBar
        mode={mode}
        host={host}
        url={doc.url}
        busy={busy}
        overridden={doc.embed?.user_override != null}
        onOpenExternal={() => window.open(doc.url, '_blank', 'noopener,noreferrer')}
        onToggle={() => setMode(mode === 'iframe' ? 'stream' : 'iframe')}
        onReset={() => setMode(null)}
      />
      {mode === 'iframe' ? (
        <iframe
          key={doc.url}
          src={doc.url}
          title={doc.title}
          className="flex-1 w-full border-0 bg-white"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        />
      ) : (
        <FallbackCard
          title={doc.title}
          host={host}
          url={doc.url}
          onOpenExternal={() => window.open(doc.url, '_blank', 'noopener,noreferrer')}
          onTryEmbed={() => setMode('iframe')}
          busy={busy}
        />
      )}
    </div>
  );
}

function ModeBar({
  mode, host, url, busy, overridden, onOpenExternal, onToggle, onReset,
}: {
  mode: 'iframe' | 'stream';
  host: string;
  url: string;
  busy: boolean;
  overridden: boolean;
  onOpenExternal: () => void;
  onToggle: () => void;
  onReset: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 px-2 py-1 border-b border-[var(--border-default)] text-xs">
      <span className="truncate opacity-70 flex-1" title={url}>{host}</span>
      {overridden && (
        <button
          onClick={onReset}
          disabled={busy}
          className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)]"
          title={t('artifacts.url.reset', 'Reset to auto')}
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      )}
      <button
        onClick={onToggle}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded opacity-70 hover:opacity-100 hover:bg-[var(--bg-secondary)]"
        title={
          mode === 'iframe'
            ? t('artifacts.url.switchToExternal', "Page not loading? Switch to the open-in-browser card")
            : t('artifacts.url.switchToEmbed', 'Try embedding this page inline')
        }
      >
        <MonitorPlay className="w-3.5 h-3.5" />
        <span>{mode === 'iframe' ? t('artifacts.url.inlineMode', 'Inline') : t('artifacts.url.externalMode', 'External')}</span>
      </button>
      <button
        onClick={onOpenExternal}
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)]"
        title={t('artifacts.url.openExternal', 'Open in new window')}
      >
        <ExternalLink className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

function FallbackCard({
  title, host, url, onOpenExternal, onTryEmbed, busy,
}: {
  title: string;
  host: string;
  url: string;
  onOpenExternal: () => void;
  onTryEmbed: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
      <SquareArrowOutUpRight className="w-10 h-10 opacity-40" />
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs opacity-60 mt-1 break-all">{host}</div>
      </div>
      <p className="text-xs opacity-70 max-w-sm">
        {t(
          'artifacts.url.cannotEmbed',
          "This site can't be shown inline — it refuses to be embedded (many large sites like Google do). Open it in your browser, or try embedding it anyway. Full in-app viewing of these sites is coming with the built-in browser.",
        )}
      </p>
      <div className="flex items-center gap-2">
        <button
          onClick={onOpenExternal}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-[var(--bg-secondary)] hover:opacity-90 text-sm"
        >
          <ExternalLink className="w-4 h-4" />
          {t('artifacts.url.openInNewWindow', 'Open in new window')}
        </button>
        <button
          onClick={onTryEmbed}
          disabled={busy}
          className="px-3 py-1.5 rounded text-sm opacity-70 hover:opacity-100"
          title={url}
        >
          {t('artifacts.url.tryEmbedAnyway', 'Try embedding anyway')}
        </button>
      </div>
    </div>
  );
}

function safeHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}
