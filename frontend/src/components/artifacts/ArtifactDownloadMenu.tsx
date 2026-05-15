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
 */

import { Download } from 'lucide-react';
import type { Artifact } from '@/types/artifact';
import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';

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
  const isChart = artifact.kind === 'application/vnd.echarts+json';
  const { url } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const ext = KIND_TO_EXT[artifact.kind] ?? 'bin';

  const exportChartImage = (type: 'png' | 'jpeg') => {
    const instance = useArtifactStore.getState().chartInstances[artifact.artifact_id];
    if (!instance) {
      window.alert('Chart is still loading. Please try again in a moment.');
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
  };

  return (
    <details className="relative">
      <summary
        className="cursor-pointer text-xs opacity-60 hover:opacity-100 px-2 py-1 select-none list-none flex items-center gap-1"
        title="Download / Export"
      >
        <Download className="w-3.5 h-3.5" />
      </summary>
      <div
        className="absolute right-0 top-full mt-1 bg-[var(--bg-primary)] border border-[var(--border-default)] py-1 z-20 min-w-[200px] text-sm shadow-lg"
        role="menu"
      >
        {isChart && (
          <>
            <button
              onClick={() => exportChartImage('png')}
              className="block w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]"
              role="menuitem"
            >
              Export as PNG
            </button>
            <button
              onClick={() => exportChartImage('jpeg')}
              className="block w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)]"
              role="menuitem"
            >
              Export as JPEG
            </button>
            <div className="my-1 border-t border-[var(--border-default)]" />
          </>
        )}
        {url ? (
          <a
            href={url}
            download={safeFilename(artifact.title, ext)}
            className="block px-3 py-1.5 hover:bg-[var(--bg-secondary)] no-underline text-[var(--text-primary)]"
            role="menuitem"
          >
            Download original (.{ext})
          </a>
        ) : (
          <span className="block px-3 py-1.5 opacity-50">Preparing download…</span>
        )}
      </div>
    </details>
  );
}
