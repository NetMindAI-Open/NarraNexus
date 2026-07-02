/**
 * @file_name: ArtifactPreviewCard.tsx
 * @description: Inline thumbnail card rendered inside chat messages when a
 * tool result references an artifact. Clicking opens the ArtifactColumn and
 * focuses the corresponding tab.
 *
 * Renders real thumbnails for image/csv/markdown; placeholder labels for
 * chart/html/pdf so the chat thread does not have to fetch the full artifact
 * eagerly for kinds that require complex rendering environments.
 *
 * Pointer model: thumbnail content is fetched via the token-protected
 * public URL (minted by `useArtifactRawUrl`).
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Artifact } from '@/types/artifact';
import { useArtifactStore } from '@/stores';
import { fetchArtifactText, fetchArtifactBlobUrl } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';

interface Props {
  artifact: Artifact;
}

export default function ArtifactPreviewCard({ artifact }: Props) {
  const { t } = useTranslation();
  const setActive = useArtifactStore((s) => s.setActive);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);
  const { url } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [csvHead, setCsvHead] = useState<string[][] | null>(null);
  const [mdHead, setMdHead] = useState<string | null>(null);
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) return;
    const isText = artifact.kind === 'text/csv' || artifact.kind === 'text/markdown';
    const isImage = artifact.kind === 'image/png' || artifact.kind === 'image/jpeg';
    if (!isText && !isImage) return;

    let cancelled = false;
    let createdBlobUrl: string | null = null;

    (async () => {
      setPreviewError(null);
      try {
        if (isImage) {
          const blobUrl = await fetchArtifactBlobUrl(url);
          if (cancelled) {
            URL.revokeObjectURL(blobUrl);
            return;
          }
          createdBlobUrl = blobUrl;
          setImageSrc(blobUrl);
        } else {
          const t = await fetchArtifactText(url);
          if (cancelled) return;
          if (artifact.kind === 'text/csv') {
            setCsvHead(t.split(/\r?\n/).slice(0, 5).map((row) => row.split(',')));
          } else {
            setMdHead(t.slice(0, 200) + (t.length > 200 ? '…' : ''));
          }
        }
      } catch (e) {
        if (!cancelled) setPreviewError(String(e));
      }
    })();

    return () => {
      cancelled = true;
      if (createdBlobUrl) URL.revokeObjectURL(createdBlobUrl);
    };
  }, [artifact.kind, url]);

  const open = () => {
    setCollapsed(false);
    setActive(artifact.artifact_id);
  };

  return (
    <button
      onClick={open}
      className="w-full max-w-md flex flex-col gap-2 p-3 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--bg-secondary)] text-left"
    >
      <div className="text-xs uppercase opacity-60">{artifact.kind}</div>
      <div className="text-sm font-semibold">{artifact.title}</div>
      <div className="min-h-[80px]">
        {(artifact.kind === 'image/png' || artifact.kind === 'image/jpeg') && imageSrc && (
          <img
            src={imageSrc}
            alt={artifact.title}
            className="max-h-24 object-contain"
          />
        )}
        {artifact.kind === 'text/csv' && csvHead && (
          <table className="text-xs border-collapse">
            <tbody>
              {csvHead.map((row, i) => (
                <tr key={i}>
                  {row.slice(0, 5).map((c, j) => (
                    <td key={j} className="border border-[var(--border-default)] px-1">
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {artifact.kind === 'text/markdown' && mdHead && (
          <p className="text-xs opacity-80 whitespace-pre-line">{mdHead}</p>
        )}
        {artifact.kind === 'application/vnd.echarts+json' && (
          <p className="text-xs opacity-60">{t('artifacts.preview.chart')}</p>
        )}
        {artifact.kind === 'text/html' && (
          <p className="text-xs opacity-60">{t('artifacts.preview.html')}</p>
        )}
        {artifact.kind === 'application/pdf' && (
          <p className="text-xs opacity-60">{t('artifacts.preview.pdf')}</p>
        )}
      </div>
      {previewError && (
        <p className="text-xs text-red-400/80">{t('artifacts.preview.unavailable', { error: previewError })}</p>
      )}
      <div className="text-xs opacity-50">{t('artifacts.preview.open')}</div>
    </button>
  );
}
