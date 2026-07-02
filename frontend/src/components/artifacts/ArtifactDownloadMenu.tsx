/**
 * @file_name: ArtifactDownloadMenu.tsx
 * @description: Per-artifact download / export dropdown.
 *
 * For chart artifacts (application/vnd.echarts+json), exposes:
 *   - Export as PNG  (via echarts.getDataURL)
 *   - Export as JPEG (via echarts.getDataURL with white background)
 *   - Download original JSON
 *
 * For all other kinds, just exposes "Download original" — a vanilla
 * <a download> against the token-protected public raw URL.
 *
 * The chart export depends on a live ECharts instance that ChartRenderer
 * registers into artifactStore.chartInstances on mount. If the menu is
 * opened before the chart has finished mounting/loading, PNG/JPEG buttons
 * surface a small "chart not ready" hint instead of silently failing.
 *
 * Pointer model: the download URL is the token-protected directory URL
 * minted via `useArtifactRawUrl`. The TTL is generous (2h) so a click much
 * later still works for the typical session.
 *
 * Layout model: the dropdown panel is rendered via createPortal into
 * document.body and positioned with fixed coordinates derived from the
 * trigger's bounding rect. This is required because every ancestor of the
 * artifact column (MainLayout <main>/<group>, ArtifactColumn <aside>) sets
 * `overflow-hidden` for flex-sizing correctness — a plain absolutely
 * positioned child would be clipped to a tiny sliver. The portal escapes
 * that clipping chain, mirroring how Dialog/ArtifactZoomModal mount.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';
import { Download } from 'lucide-react';
import type { Artifact } from '@/types/artifact';
import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { downloadFile } from '@/lib/download';

const KIND_TO_EXT: Record<string, string> = {
  'text/html': 'html',
  'application/vnd.echarts+json': 'json',
  'text/csv': 'csv',
  'text/markdown': 'md',
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'application/pdf': 'pdf',
};

function safeFilename(title: string, ext: string): string {
  // Strip path-illegal punctuation. Control chars are filtered by codepoint
  // separately to avoid the no-control-regex lint rule.
  const stripped = title.replace(/[/\\?%*:|"<>]/g, '_');
  const noControl = Array.from(stripped)
    .filter((ch) => {
      const code = ch.charCodeAt(0);
      return code >= 32 && code !== 127;
    })
    .join('');
  const cleaned = noControl.trim().slice(0, 100);
  return `${cleaned || 'artifact'}.${ext}`;
}

interface Props {
  artifact: Artifact;
}

export default function ArtifactDownloadMenu({ artifact }: Props) {
  const { t } = useTranslation();
  const isChart = artifact.kind === 'application/vnd.echarts+json';
  const { url } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const ext = KIND_TO_EXT[artifact.kind] ?? 'bin';

  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // Fixed-position coordinates for the portal-mounted panel, right-aligned
  // to the trigger's right edge (matching the old `right-0 top-full`).
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });

  const recompute = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPos({
      top: rect.bottom + 4, // mt-1 ≈ 4px gap below the trigger
      right: window.innerWidth - rect.right,
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    recompute();
    const onScroll = () => recompute();
    const onResize = () => recompute();
    // capture phase so we also catch scrolls on inner overflow containers
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    const onPointerDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, recompute]);

  const exportChartImage = (type: 'png' | 'jpeg') => {
    const instance = useArtifactStore.getState().chartInstances[artifact.artifact_id];
    if (!instance) {
      window.alert(t('artifacts.download.chartLoading'));
      return;
    }
    const dataUrl = instance.getDataURL({
      type,
      backgroundColor: type === 'jpeg' ? '#ffffff' : 'transparent',
      pixelRatio: 2,
    });
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = safeFilename(artifact.title, type === 'jpeg' ? 'jpg' : 'png');
    document.body.appendChild(a);
    a.click();
    a.remove();
    setOpen(false);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="cursor-pointer text-xs opacity-60 hover:opacity-100 px-2 py-1 select-none flex items-center gap-1"
        title={t('artifacts.download.title')}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Download className="w-3.5 h-3.5" />
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="fixed bg-[var(--bg-primary)] border border-[var(--border-default)] py-1 z-[1000] min-w-[200px] text-sm shadow-lg"
            style={{ top: pos.top, right: pos.right }}
            role="menu"
          >
            {isChart && (
              <>
                <button
                  onClick={() => exportChartImage('png')}
                  className="block w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]"
                  role="menuitem"
                >
                  {t('artifacts.download.exportPng')}
                </button>
                <button
                  onClick={() => exportChartImage('jpeg')}
                  className="block w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]"
                  role="menuitem"
                >
                  {t('artifacts.download.exportJpeg')}
                </button>
                <div className="my-1 border-t border-[var(--border-default)]" />
              </>
            )}
            {url ? (
              <button
                onClick={() => {
                  setOpen(false);
                  downloadFile({ url, filename: safeFilename(artifact.title, ext) });
                }}
                className="block w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]"
                role="menuitem"
              >
                {t('artifacts.download.original', { ext })}
              </button>
            ) : (
              <span className="block px-3 py-1.5 opacity-50">{t('artifacts.download.preparing')}</span>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
