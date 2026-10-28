/**
 * @file_name: ResidentTextEditor.tsx
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: The resident editing surface (kindRegistry editSurface
 * "resident-editor"): the source text IS the render, always editable — no
 * view/edit mode anywhere (no-mode framework, spec A §1). Used by CsvRenderer
 * today; any future code/plain kind mounts this same component.
 *
 * All state rules live in useArtifactEditor; this component owns only the
 * chrome: CodeMirror body, save bar (explicit save — Cmd/Ctrl+S or button),
 * conflict banner (two-choice), draft-restored banner, and mirroring `dirty`
 * into the artifact store for the tab-strip dot.
 */

import { useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import CodeMirror from '@uiw/react-codemirror';
import type { Artifact } from '@/types/artifact';
import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifactEditor } from '@/hooks/useArtifactEditor';
import { ConflictBanner, DraftRestoredBanner, DraftUnavailableBanner } from './editorBanners';

interface Props {
  artifact: Artifact;
  /** Token-protected raw directory URL (from useArtifactRawUrl). */
  url: string | null;
  /** Bubble load errors up so the host renderer can run its heal flow. */
  onLoadError?: (message: string) => void;
}

export default function ResidentTextEditor({ artifact, url, onLoadError }: Props) {
  const { t } = useTranslation();
  const editor = useArtifactEditor(artifact, url);
  const setEditorDirty = useArtifactStore((s) => s.setEditorDirty);

  useEffect(() => {
    setEditorDirty(artifact.artifact_id, editor.dirty);
    return () => setEditorDirty(artifact.artifact_id, false);
  }, [editor.dirty, artifact.artifact_id, setEditorDirty]);

  useEffect(() => {
    if (editor.status === 'error' && editor.error && onLoadError) onLoadError(editor.error);
  }, [editor.status, editor.error, onLoadError]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (editor.dirty && !editor.saving && !editor.conflict) void editor.save();
      }
    },
    [editor],
  );

  if (editor.status === 'loading') {
    return <div className="p-4 opacity-60">{t('artifacts.loadingRenderer')}</div>;
  }
  if (editor.status === 'error') {
    return <div className="p-4 text-red-400">{t('artifacts.editor.loadError', { error: editor.error })}</div>;
  }

  return (
    <div className="flex flex-col h-full" onKeyDown={onKeyDown}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border-default)] text-xs shrink-0">
        {editor.dirty ? (
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" aria-hidden />
            {t('artifacts.editor.unsavedChanges')}
          </span>
        ) : (
          <span className="opacity-50">{t('artifacts.editor.saved')}</span>
        )}
        <div className="flex-1" />
        <button
          onClick={() => void editor.save()}
          disabled={!editor.dirty || editor.saving || editor.conflict !== null}
          className="px-2 py-0.5 border border-[var(--border-default)] disabled:opacity-40 hover:bg-[var(--nm-paper-warm)]"
        >
          {editor.saving ? t('artifacts.editor.saving') : t('artifacts.editor.save')}
        </button>
      </div>
      {editor.draftRestored && <DraftRestoredBanner />}
      {editor.draftUnavailable && <DraftUnavailableBanner />}
      {editor.conflict && (
        <ConflictBanner
          onOverwrite={() => void editor.overwriteConflict()}
          onDiscard={() => void editor.discardConflict()}
        />
      )}
      <div className="flex-1 min-h-0 overflow-auto">
        <CodeMirror
          value={editor.text}
          onChange={editor.setText}
          basicSetup={{ lineNumbers: true, foldGutter: false, highlightActiveLine: true }}
          className="text-sm h-full"
        />
      </div>
    </div>
  );
}
