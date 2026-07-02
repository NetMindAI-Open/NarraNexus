/**
 * @file_name: ArtifactInlineBadge.tsx
 * @description: One-line chip rendered at the foot of an assistant message
 * when that turn registered (or re-registered) an artifact. Replaces the
 * larger ArtifactPreviewCard that used to flash on every refresh.
 *
 * Design intent: minimum visual disruption. The right-side ArtifactColumn
 * is the canonical place to view content; this badge is just an
 * affordance — "this turn produced X, click to jump there".
 *
 * Why no preview: ArtifactPreviewCard fetched the artifact bytes via the
 * token-protected raw URL, ran CSV head-row parsing, image blob loading,
 * etc. on every render. For HTML / Chart / PDF kinds that work was thrown
 * away (placeholder text rendered). Re-register (refresh signal) made it
 * fire again, producing the visible "flash and disappear" that motivated
 * this rewrite. The badge does zero raw-URL fetches.
 */

import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip } from 'lucide-react';
import type { Artifact } from '@/types/artifact';
import { useArtifactStore } from '@/stores';

interface Props {
  artifact: Artifact;
}

function ArtifactInlineBadgeImpl({ artifact }: Props) {
  const { t } = useTranslation();
  const setActive = useArtifactStore((s) => s.setActive);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);

  const open = () => {
    setCollapsed(false);
    setActive(artifact.artifact_id);
  };

  return (
    <button
      onClick={open}
      title={t('artifacts.openBadge', { title: artifact.title, kind: artifact.kind })}
      className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-[family-name:var(--font-mono)] border border-[var(--border-subtle)] hover:border-[var(--text-tertiary)] hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] transition-colors max-w-full"
    >
      <Paperclip className="w-3 h-3 shrink-0 opacity-60" />
      <span className="truncate">{artifact.title || artifact.artifact_id}</span>
      <span className="opacity-50 shrink-0">↗</span>
    </button>
  );
}

const ArtifactInlineBadge = memo(ArtifactInlineBadgeImpl, (a, b) =>
  a.artifact.artifact_id === b.artifact.artifact_id &&
  a.artifact.title === b.artifact.title &&
  a.artifact.updated_at === b.artifact.updated_at,
);

export default ArtifactInlineBadge;
