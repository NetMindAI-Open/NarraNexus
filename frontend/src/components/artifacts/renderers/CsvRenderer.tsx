/**
 * @file_name: CsvRenderer.tsx
 * @description: Renderer for text/csv artifacts — a resident text editor
 * (spec A §1: csv is render=source, view IS edit; the table projection was
 * retired with the no-mode framework, a grid surface is a v2 enhancement).
 *
 * This component owns only the heal flow (broken pointer → candidates modal)
 * and the raw-url mint; everything editable lives in ResidentTextEditor.
 */

import { useCallback, useEffect, useRef } from 'react';
import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import ArtifactHealModal from '../ArtifactHealModal';
import ResidentTextEditor from './ResidentTextEditor';

interface Props {
  artifact: Artifact;
}

export default function CsvRenderer({ artifact }: Props) {
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  // attempt() via ref so the error callback's identity stays stable — see
  // HtmlRenderer for the bug story (Dismiss-modal loop, 2026-05-25).
  const attemptRef = useRef(heal.attempt);
  useEffect(() => {
    attemptRef.current = heal.attempt;
  }, [heal.attempt]);

  useEffect(() => {
    if (heal.recoveryVersion > 0) reload();
  }, [heal.recoveryVersion, reload]);

  const onLoadError = useCallback((message: string) => {
    if (message.includes('fetch failed: 410')) attemptRef.current();
  }, []);

  if (urlError) {
    return <div className="p-4 text-red-400">Failed to load: {urlError}</div>;
  }

  return (
    <>
      <ResidentTextEditor artifact={artifact} url={url} onLoadError={onLoadError} />
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
