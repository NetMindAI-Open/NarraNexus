/**
 * @file_name: OfficeWatchViewer.tsx
 * @description: Renders a LIVE Office-document preview (officecli watch) in an
 * iframe pointed at the backend SSE reverse-proxy
 * (`/api/office-watch-proxy/{port}/`). The proxied page auto-refreshes over
 * SSE as the agent keeps editing the document with officecli — so the deck
 * takes shape in real time.
 *
 * Refresh model is HYBRID:
 *   - Primary: SSE. When officecli's per-file resident is shared (the agent
 *     edits via the SAME path string the watch was started with), the watch
 *     pushes content frames and the page re-renders smoothly. The proxy-
 *     injected shim postMessages us `officewatch-content` on each such frame.
 *   - Fallback: mtime poll. officecli keys its resident by the raw (cwd, path)
 *     string, so if the agent ever edits via a different string the watch
 *     never live-refreshes. We poll the file's mtime; when it advances but no
 *     content frame arrived, we reload the iframe (a cache-busted src — the
 *     watch page's own GET always renders the current document). This keeps the
 *     preview correct even when SSE silently breaks, WITHOUT flickering while
 *     SSE is working.
 *
 * Kept separate from the artifact renderers (which are static pointer-to-file
 * with token URLs + self-heal); a live server is a different beast.
 *
 * Tauri desktop: WKWebView blocks a mixed-content HTTP iframe from the
 * https://tauri.localhost origin, so we route the watch page through the
 * `officewatch://` custom scheme (Rust proxies it to the backend — see
 * office_watch_scheme.rs). That scheme can't stream SSE, so on desktop the
 * mtime-poll → reload path below is the ONLY update mechanism: no live SSE
 * frames arrive, `lastContentSseAt` stays 0, and every mtime advance reloads
 * the iframe. Result: the preview updates each time the doc changes (poll
 * cadence), just not frame-smooth like the browser.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isTauri } from '@/lib/tauri';
import { officeWatchApi } from '@/services/officeWatchApi';
import {
  buildAddCommand,
  buildMoveCommand,
  buildRemoveCommands,
  buildSetFormulaCommand,
  buildSetPropsCommands,
  buildSetSrcCommand,
  buildSetTextCommand,
  cellFromPath,
  isPicturePath,
  parseSelectionMessage,
  slideIndexFromPath,
} from '@/lib/artifactEditing/officeEditCommands';
import type { Artifact } from '@/types/artifact';

interface Props {
  artifact: Artifact;
}

// Poll cadence for the mtime fallback, and how long after an SSE content frame
// we treat the live path as handling updates (so the fallback stays quiet).
const VERSION_POLL_MS = 2000;
const SSE_FRESH_MS = 4000;

/** Mint the signed iframe URL, retrying a few times — a watch that just
 * idle-died needs a moment to restart, and we'd rather retry than flash the
 * "could not open" error (the fourth-turn symptom). */
async function openWithRetry(artifactId: string, attempts = 4): Promise<string> {
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await officeWatchApi.open(artifactId);
    } catch (e) {
      lastErr = e;
      await new Promise((r) => setTimeout(r, 400 * (i + 1)));
    }
  }
  throw lastErr;
}

/** Desktop: turn the backend's http open URL into an `officewatch://` URL so
 * WKWebView loads it (the custom scheme dodges mixed-content blocking; Rust
 * proxies it to the backend). Same path, different scheme. */
function toDesktopScheme(httpUrl: string): string {
  try {
    const u = new URL(httpUrl);
    return `officewatch://localhost${u.pathname}${u.search}`;
  } catch {
    return httpUrl;
  }
}

// An artifact renderer (registered in ArtifactRenderer.RENDERER_BY_KIND for the
// office-live kind). Office docs render as a LIVE preview: we ask the backend
// to (re)start a watch for this artifact's file and point an iframe at the
// token-signed proxy URL — auto-refreshes over SSE as the agent edits.
export default function OfficeWatchViewer({ artifact }: Props) {
  const { t } = useTranslation();
  // An <iframe src> navigation can't send X-User-Id, so we first mint a signed
  // URL (token in the path) via the session-authed `open` endpoint, then point
  // the iframe at it. The watch page's own relative sub-requests keep the token
  // prefix (via the backend-injected <base>).
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Manual retry: an open() failure used to be a permanent dead end until the
  // user switched tabs. Bumping this re-runs the open effect, so a transient
  // watch failure (still coming up, mid-restart) is user-recoverable.
  const [retryNonce, setRetryNonce] = useState(0);
  // ── T1 direct-edit bar state (spec B §3.3) ──
  const [selection, setSelection] = useState<string[]>([]);
  const [editText, setEditText] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const [officeLock, setOfficeLock] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  // Wall-clock of the last CONTENT frame the shim reported over SSE, and the
  // last file mtime we've reflected — the two inputs to the fallback decision.
  const lastContentSseAt = useRef(0);
  const knownMtime = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;
    lastContentSseAt.current = 0;
    knownMtime.current = null;

    // The shim inside the watch page postMessages us on each content frame; note
    // it so the mtime fallback knows SSE is already rendering (no double reload).
    const onMessage = (ev: MessageEvent) => {
      if (ev.source !== iframeRef.current?.contentWindow) return;
      if (ev.data && ev.data.type === 'officewatch-content') {
        lastContentSseAt.current = Date.now();
      }
      if (ev.data && ev.data.type === 'officewatch-selection') {
        setSelection(parseSelectionMessage(String(ev.data.body ?? '')));
        setEditText(null);
      }
    };
    window.addEventListener('message', onMessage);

    (async () => {
      // Reset view for the (re)opened artifact. Inside the async body — not the
      // effect body — so it isn't a synchronous cascading setState.
      setSrc(null);
      setError(null);
      let base: string;
      try {
        // Open by ARTIFACT — the backend (re)starts the watch on demand, so
        // this works after a refresh or once the previous watch idle-stopped.
        base = await openWithRetry(artifact.artifact_id);
      } catch {
        if (!cancelled) setError('open');
        return;
      }
      if (cancelled) return;
      // Desktop loads the watch page through the officewatch:// custom scheme
      // (mixed-content dodge); browser uses the http URL directly.
      if (isTauri()) base = toDesktopScheme(base);
      setSrc(base);
      try {
        const v0 = await officeWatchApi.version(artifact.artifact_id);
        if (!cancelled) {
          knownMtime.current = v0.mtime;
          setOfficeLock(Boolean(v0.lock));
        }
      } catch {
        /* best-effort baseline; the interval will set it */
      }
      interval = setInterval(async () => {
        try {
          const v = await officeWatchApi.version(artifact.artifact_id);
          if (cancelled) return;
          setOfficeLock(Boolean(v.lock));
          if (knownMtime.current === null) {
            knownMtime.current = v.mtime;
            return;
          }
          if (v.mtime > knownMtime.current) {
            knownMtime.current = v.mtime;
            // SSE already rendered this change → leave the smooth path alone.
            if (Date.now() - lastContentSseAt.current < SSE_FRESH_MS) return;
            // SSE missed it → reload. Go through `open` again (not a cache-bust
            // of the old URL): the watch idle-stops during quiet spells (desktop
            // has no SSE to keep it warm), so re-opening RE-ENSURES it — reusing
            // the dead port is what surfaced "watch server unavailable" (502).
            let fresh: string;
            try {
              fresh = await openWithRetry(artifact.artifact_id);
            } catch {
              return; // transient; next tick retries
            }
            if (cancelled) return;
            if (isTauri()) fresh = toDesktopScheme(fresh);
            base = fresh;
            const sep = base.includes('?') ? '&' : '?';
            setSrc(`${base}${sep}_r=${Math.floor(v.mtime * 1000)}`);
          }
        } catch {
          /* transient (watch restarting); next tick retries */
        }
      }, VERSION_POLL_MS);
    })();

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
      window.removeEventListener('message', onMessage);
    };
  }, [artifact.artifact_id, artifact.updated_at, retryNonce]);

  // Run a T1 op batch: proxy POST → registry commit point → clear selection.
  // The preview refresh rides the watch's own SSE; no iframe reload here.
  const runEdit = async (commands: unknown[]) => {
    if (editBusy) return;
    setEditBusy(true);
    setEditNotice(null);
    try {
      const res = await officeWatchApi.sendBatch(artifact.artifact_id, commands);
      if (!res.ok) {
        setEditNotice(t('artifacts.officeEdit.opFailed', { defaultValue: 'Edit failed.' }));
        return;
      }
      await officeWatchApi.commitEdit(artifact.agent_id, artifact.artifact_id);
      setSelection([]);
      setEditText(null);
    } catch (e) {
      console.error('office edit failed', e);
      setEditNotice(t('artifacts.officeEdit.opFailed', { defaultValue: 'Edit failed.' }));
    } finally {
      setEditBusy(false);
    }
  };

  const startTextEdit = async () => {
    if (selection.length !== 1) return;
    const el = await officeWatchApi.getElement(artifact.artifact_id, selection[0]);
    setEditText(el?.text ?? '');
  };

  // '=' in a CELL is a formula (verified prop, T2); everywhere else it is text.
  const buildCellValueCommand = (path: string, value: string) =>
    value.startsWith('=') && cellFromPath(path)
      ? buildSetFormulaCommand(path, value)
      : buildSetTextCommand(path, value);

  const soleSlide = selection.length === 1 ? slideIndexFromPath(selection[0]) : null;
  const soleCell = selection.length === 1 ? cellFromPath(selection[0]) : null;
  const solePicture = selection.length === 1 && isPicturePath(selection[0]);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const replaceImage = async (file: File) => {
    setEditBusy(true);
    setEditNotice(null);
    try {
      const path = await officeWatchApi.uploadAsset(
        artifact.agent_id, artifact.artifact_id, file,
      );
      setEditBusy(false);
      await runEdit([buildSetSrcCommand(selection[0], path)]);
    } catch (e) {
      console.error('office image replace failed', e);
      setEditNotice(t('artifacts.officeEdit.opFailed', { defaultValue: 'Edit failed.' }));
      setEditBusy(false);
    }
  };

  const editBar = selection.length > 0 && src && !error && (
    <div className="flex items-center gap-2 border-b border-[var(--border-default)] px-3 py-1.5 text-xs shrink-0 flex-wrap">
      <span className="opacity-60">
        {t('artifacts.officeEdit.selected', {
          count: selection.length,
          defaultValue: '{{count}} selected',
        })}
      </span>
      {officeLock ? (
        <span className="text-amber-600">
          {t('artifacts.officeEdit.lockNotice', {
            defaultValue: 'Open in a desktop Office app — direct edits are paused.',
          })}
        </span>
      ) : editText !== null ? (
        <>
          <input
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            className="flex-1 min-w-[10rem] border border-[var(--border-default)] bg-transparent px-2 py-0.5"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runEdit([buildCellValueCommand(selection[0], editText)]);
              if (e.key === 'Escape') setEditText(null);
            }}
          />
          <button
            disabled={editBusy}
            onClick={() => void runEdit([buildCellValueCommand(selection[0], editText)])}
            className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
          >
            {t('artifacts.officeEdit.apply', { defaultValue: 'Apply' })}
          </button>
          <button
            onClick={() => setEditText(null)}
            className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)]"
          >
            {t('artifacts.officeEdit.cancel', { defaultValue: 'Cancel' })}
          </button>
        </>
      ) : (
        <>
          {selection.length === 1 && (
            <button
              disabled={editBusy}
              onClick={() => void startTextEdit()}
              className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
            >
              {t('artifacts.officeEdit.editText', { defaultValue: 'Edit text' })}
            </button>
          )}
          <button
            disabled={editBusy}
            onClick={() => void runEdit(buildSetPropsCommands(selection, { bold: true }))}
            className="px-2 py-0.5 border border-[var(--border-default)] font-bold hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
          >
            B
          </button>
          <button
            disabled={editBusy}
            onClick={() => void runEdit(buildSetPropsCommands(selection, { italic: true }))}
            className="px-2 py-0.5 border border-[var(--border-default)] italic hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
          >
            I
          </button>
          <label className="px-1 cursor-pointer" title={t('artifacts.officeEdit.color', { defaultValue: 'Text color' })}>
            <input
              type="color"
              className="w-5 h-5 border-0 bg-transparent cursor-pointer"
              disabled={editBusy}
              onChange={(e) =>
                void runEdit(
                  buildSetPropsCommands(selection, {
                    color: e.target.value.replace('#', '').toUpperCase(),
                  }),
                )
              }
            />
          </label>
          {soleSlide !== null && (
            <>
              <button
                disabled={editBusy || soleSlide <= 1}
                onClick={() => void runEdit([buildMoveCommand(selection[0], soleSlide - 2)])}
                className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
              >
                {t('artifacts.officeEdit.moveUp', { defaultValue: 'Move up' })}
              </button>
              <button
                disabled={editBusy}
                onClick={() => void runEdit([buildMoveCommand(selection[0], soleSlide)])}
                className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
              >
                {t('artifacts.officeEdit.moveDown', { defaultValue: 'Move down' })}
              </button>
            </>
          )}
          {soleCell !== null && (
            <>
              <button
                disabled={editBusy}
                onClick={() => void runEdit([buildAddCommand(soleCell.sheet, 'row', soleCell.row)])}
                className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
              >
                {t('artifacts.officeEdit.addRow', { defaultValue: '+ Row' })}
              </button>
              <button
                disabled={editBusy}
                onClick={() => void runEdit([buildAddCommand(soleCell.sheet, 'column')])}
                className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
              >
                {t('artifacts.officeEdit.addCol', { defaultValue: '+ Column' })}
              </button>
            </>
          )}
          {solePicture && (
            <>
              <button
                disabled={editBusy}
                onClick={() => imageInputRef.current?.click()}
                className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40"
              >
                {t('artifacts.officeEdit.replaceImage', { defaultValue: 'Replace image' })}
              </button>
              <input
                ref={imageInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = '';
                  if (f) void replaceImage(f);
                }}
              />
            </>
          )}
          <button
            disabled={editBusy}
            onClick={() => void runEdit(buildRemoveCommands(selection))}
            className="px-2 py-0.5 border border-red-500/50 text-red-500 hover:bg-red-500/10 disabled:opacity-40"
          >
            {t('artifacts.officeEdit.delete', { defaultValue: 'Delete' })}
          </button>
        </>
      )}
      {editNotice && <span className="text-red-500">{editNotice}</span>}
    </div>
  );

  return (
    // h-full fills the artifact content area (a flex-1 block); the iframe then
    // takes flex-1 within this column so it reaches the bottom.
    <div className="flex h-full flex-col min-h-0">
      {editBar}
      {error ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-sm text-[var(--color-error)]">
          <span>
            {t('officeWatch.loadError', { defaultValue: 'Could not open the live preview.' })}
          </span>
          <button
            type="button"
            onClick={() => {
              setError(null);
              setRetryNonce((n) => n + 1);
            }}
            className="rounded-md border border-[var(--border-default)] px-3 py-1 text-[var(--text-secondary)] transition-colors hover:text-[var(--color-carbon)]"
          >
            {t('officeWatch.retry', { defaultValue: 'Retry' })}
          </button>
        </div>
      ) : !src ? (
        <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-[var(--text-tertiary)]">
          {t('officeWatch.loading', { defaultValue: 'Opening live preview…' })}
        </div>
      ) : (
        <iframe
          key={`${artifact.artifact_id}-${artifact.updated_at}`}
          ref={iframeRef}
          title={artifact.title}
          sandbox="allow-scripts"
          src={src}
          referrerPolicy="no-referrer"
          className="w-full flex-1 min-h-0 border-0 bg-white"
        />
      )}
    </div>
  );
}
