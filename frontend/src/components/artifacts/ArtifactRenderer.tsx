/**
 * @file_name: ArtifactRenderer.tsx
 * @description: Shared renderer dispatch for artifact content — the renderer
 * itself comes from the kind capability registry (kindRegistry.ts), this
 * component only owns the Suspense/scroll-box shell.
 *
 * Pulled out of ArtifactColumn so the embedded column view AND the
 * zoom modal can render artifacts through the same lazy-loaded renderer
 * chunks. Two renderer instances for the same kind do NOT trigger duplicate
 * chunk downloads — React.lazy memoises by import.
 *
 * Pointer model: renderers no longer take a `version` prop; they mint a view
 * token via `useArtifactRawUrl` and load content from the public raw route.
 */

import { Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import type { Artifact } from '@/types/artifact';
import { ARTIFACT_KINDS, useRegistryEntries } from '@/platform/registries';

interface Props {
  artifact: Artifact;
  /**
   * true (default, the column): wrap the renderer in an h-full/w-full
   * overflow-auto box that owns ALL scrolling — the column's content box is
   * overflow-hidden by design (drag-freeze relies on clipping) and renderer
   * roots are auto-height, so without this bound nothing ever overflows a
   * scrollable container and content past the first screen is clipped.
   *
   * false (the zoom modal): no wrapper. The modal's scale-sizer layers have
   * fixed heights and its own outer overflow-auto container must carry the
   * scrolling — renderers overflow it naturally, and the zoom mechanism
   * depends on that overflow. A bounded box here would clamp content to one
   * screen and nest a second scrollbar inside the scaled layer.
   */
  bounded?: boolean;
}

export default function ArtifactRenderer({ artifact, bounded = true }: Props) {
  const { t } = useTranslation();
  // Subscribed: a plugin registering this kind after the artifact opened re-renders it.
  void useRegistryEntries(ARTIFACT_KINDS);
  const Renderer = ARTIFACT_KINDS.get(artifact.kind)?.renderer;
  if (!Renderer) {
    return <div className="p-4 opacity-60">{t('artifacts.unsupportedKind', { kind: artifact.kind })}</div>;
  }
  const body = (
    <Suspense fallback={<div className="p-4 opacity-60">{t('artifacts.loadingRenderer')}</div>}>
      <Renderer artifact={artifact} />
    </Suspense>
  );
  if (!bounded) return body;
  // h-full/w-full, NOT absolute inset-0 — absolute would need a positioned
  // ancestor and this component's placement is the caller's concern.
  return <div className="h-full w-full overflow-auto overscroll-contain">{body}</div>;
}
