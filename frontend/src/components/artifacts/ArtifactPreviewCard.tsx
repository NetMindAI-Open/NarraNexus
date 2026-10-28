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
import { ARTIFACT_KINDS, useRegistryEntries } from '@/platform/registries';

interface Props {
  artifact: Artifact;
}

export default function ArtifactPreviewCard({ artifact }: Props) {
  const { t } = useTranslation();
  // restoreTab, not setActive: the target may be minimized, and an active
  // pointer on a hidden tab blanks the column (0802 ①⑤ family).
  const restoreTab = useArtifactStore((s) => s.restoreTab);
  const { url } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [csvHead, setCsvHead] = useState<string[][] | null>(null);
  const [mdHead, setMdHead] = useState<string | null>(null);
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  void useRegistryEntries(ARTIFACT_KINDS);
  const descriptor = ARTIFACT_KINDS.get(artifact.kind);
  const preview = descriptor?.preview ?? 'none';
  const placeholderKey = descriptor?.previewPlaceholderKey;

  useEffect(() => {
    if (!url) return;
    const isText = preview === 'csv-head' || preview === 'md-head';
    const isImage = preview === 'image';
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
          if (preview === 'csv-head') {
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
  }, [preview, url]);

  const open = () => {
    restoreTab(artifact.artifact_id);
  };

  return (
    <button
      onClick={open}
      className="w-full max-w-md flex flex-col gap-2 p-3 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--nm-paper-warm)] text-left"
    >
      <div className="text-xs uppercase opacity-60">{artifact.kind}</div>
      <div className="text-sm font-semibold">{artifact.title}</div>
      <div className="min-h-[80px]">
        {preview === 'image' && imageSrc && (
          <img
            src={imageSrc}
            alt={artifact.title}
            className="max-h-24 object-contain"
          />
        )}
        {preview === 'csv-head' && csvHead && (
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
        {preview === 'md-head' && mdHead && (
          <p className="text-xs opacity-80 whitespace-pre-line">{mdHead}</p>
        )}
        {preview === 'placeholder' && placeholderKey && (
          <p className="text-xs opacity-60">{t(placeholderKey)}</p>
        )}
      </div>
      {previewError && (
        <p className="text-xs text-red-400/80">{t('artifacts.preview.unavailable', { error: previewError })}</p>
      )}
      <div className="text-xs opacity-50">{t('artifacts.preview.open')}</div>
    </button>
  );
}
