/**
 * @file_name: ArtifactZoomModal.tsx
 * @description: Fullscreen-ish artifact viewer modal with content zoom.
 *
 * Mounted near the artifact column. Renders a portal-based overlay
 * that dims AND blurs the rest of the page, then displays the active
 * artifact at large size (95vw x 95vh) using the shared
 * ArtifactRenderer. Closes on Esc, backdrop click, or the close
 * button.
 *
 * The content inside the frame is independently zoomable (0.25x–3x) so
 * dense artifacts (wide charts, small-font HTML reports, multi-column
 * CSVs) can be scaled up, or oversized ones scaled down to fit. Zoom
 * is driven by header +/- buttons, Ctrl/Cmd + mouse wheel, and
 * Ctrl/Cmd +/-/0 keyboard shortcuts. The parent (ArtifactColumn) keys
 * this component by artifact_id, so each open is a fresh mount and the
 * zoom naturally starts back at 100%.
 *
 * Scaling uses `transform: scale()` (not CSS `zoom`): `zoom` is
 * unreliable on iframes across engines, whereas `transform` is
 * GPU-composited and works on every artifact kind including the
 * sandboxed HTML iframe. A two-layer wrapper (sizer + scaled inner)
 * keeps the `overflow-auto` scroll container's scrollbars tracking the
 * *scaled* size — see the body JSX comment for the sizing math.
 *
 * The dedicated component (instead of reusing ui/Dialog) exists for
 * three reasons:
 *   1. Dialog caps at max-w-6xl — too small for full-fidelity artifact
 *      viewing (charts, HTML, PDFs all benefit from more room).
 *   2. Dialog backdrop is opaque ink; user explicitly asked for a
 *      blurred backdrop so the page stays visually present behind.
 *   3. Header affordances differ — Download menu + zoom controls
 *      instead of a generic title + close.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';
import { X, ZoomIn, ZoomOut } from 'lucide-react';
import type { Artifact } from '@/types/artifact';
import ArtifactRenderer from './ArtifactRenderer';
import ArtifactDownloadMenu from './ArtifactDownloadMenu';

interface Props {
  artifact: Artifact | null;
  onClose: () => void;
}

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 3;
const BUTTON_STEP = 0.1;
// deltaY is in pixels; ~100 px wheel notch → 0.15 zoom change.
const WHEEL_STEP = 0.0015;

function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

export default function ArtifactZoomModal({ artifact, onClose }: Props) {
  const { t } = useTranslation();
  // Zoom starts at 100% on every mount. ArtifactColumn keys this component
  // by artifact_id, so opening a different artifact remounts it and the
  // zoom resets automatically — no reset-on-change effect needed.
  const [zoom, setZoom] = useState(1);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        if (e.key === '=' || e.key === '+') {
          e.preventDefault();
          setZoom((z) => clampZoom(z + BUTTON_STEP));
        } else if (e.key === '-') {
          e.preventDefault();
          setZoom((z) => clampZoom(z - BUTTON_STEP));
        } else if (e.key === '0') {
          e.preventDefault();
          setZoom(1);
        }
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (!artifact) return;
    document.addEventListener('keydown', handleKeyDown);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = prevOverflow;
    };
  }, [artifact, handleKeyDown]);

  // Ctrl/Cmd + wheel to zoom. Attached as a native non-passive listener
  // because React's synthetic onWheel is passive — preventDefault there is
  // a no-op, so the browser's own page-zoom would fire instead.
  useEffect(() => {
    if (!artifact) return;
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom((z) => clampZoom(z - e.deltaY * WHEEL_STEP));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [artifact]);

  if (!artifact) return null;

  const zoomOut = () => setZoom((z) => clampZoom(z - BUTTON_STEP));
  const zoomIn = () => setZoom((z) => clampZoom(z + BUTTON_STEP));
  const resetZoom = () => setZoom(1);

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label={t('artifacts.zoomModal.aria', { title: artifact.title })}
    >
      {/* Backdrop — dim + blur, click anywhere to close */}
      <div
        className="absolute inset-0 bg-[rgba(17,18,20,0.55)] backdrop-blur-md"
        onClick={onClose}
        aria-hidden
      />

      {/* Frame — 95vw x 95vh, flat ink border to match Dialog visual lang. */}
      <div
        className="relative z-10 flex flex-col bg-[var(--bg-primary)] border border-[var(--text-primary)] animate-slide-up"
        style={{ width: '95vw', height: '95vh', borderRadius: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--rule)] min-w-0">
          <h2
            className="text-[11px] font-medium uppercase font-[family-name:var(--font-mono)] tracking-[0.18em] text-[var(--text-primary)] truncate"
            title={artifact.title}
          >
            {artifact.title}
          </h2>
          <div className="flex items-center gap-1">
            {/* Zoom controls */}
            <div className="flex items-center gap-0.5 mr-1 pr-1.5 border-r border-[var(--rule)]">
              <button
                onClick={zoomOut}
                disabled={zoom <= MIN_ZOOM}
                className="w-7 h-7 flex items-center justify-center opacity-70 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors disabled:opacity-30 disabled:hover:bg-transparent"
                title={t('artifacts.zoomModal.zoomOut')}
                aria-label={t('artifacts.zoomModal.zoomOutAria')}
              >
                <ZoomOut className="w-4 h-4" />
              </button>
              <button
                onClick={resetZoom}
                className="w-12 text-[11px] font-[family-name:var(--font-mono)] tabular-nums opacity-70 hover:opacity-100 transition-opacity"
                title={t('artifacts.zoomModal.reset')}
                aria-label={t('artifacts.zoomModal.resetAria')}
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                onClick={zoomIn}
                disabled={zoom >= MAX_ZOOM}
                className="w-7 h-7 flex items-center justify-center opacity-70 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors disabled:opacity-30 disabled:hover:bg-transparent"
                title={t('artifacts.zoomModal.zoomIn')}
                aria-label={t('artifacts.zoomModal.zoomInAria')}
              >
                <ZoomIn className="w-4 h-4" />
              </button>
            </div>
            <ArtifactDownloadMenu artifact={artifact} />
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center opacity-70 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors"
              title={t('artifacts.zoomModal.close')}
              aria-label={t('artifacts.zoomModal.closeAria')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body — scroll container + two-layer zoom wrapper.

            `transform: scale()` keeps the element's *layout* box at its
            unscaled size, so a naive single wrapper would never give the
            scroll container anything to scroll. The fix:
              · sizer  — width/height = zoom·100%  → reserves the scaled
                         footprint so the scroll container shows scrollbars.
              · inner  — width/height = (100/zoom)% of the sizer  → its
                         layout box resolves back to exactly the container
                         size, then scale(zoom) blows it up to fill the
                         sizer. The artifact (incl. the HTML iframe) is
                         w-full/h-full of `inner`, so it scales with it. */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
          <div style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}>
            <div
              className="origin-top-left"
              style={{
                width: `${100 / zoom}%`,
                height: `${100 / zoom}%`,
                transform: `scale(${zoom})`,
              }}
            >
              <ArtifactRenderer artifact={artifact} />
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
