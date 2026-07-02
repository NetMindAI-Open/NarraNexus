/**
 * @file_name: ArtifactTabStrip.tsx
 * @description: Horizontally scrolling tab bar showing the user's currently
 * VISIBLE artifacts — i.e. artifacts that are not minimized. Minimized
 * artifacts surface in the header bar above (rendered by ArtifactColumn).
 *
 * Per-tab actions:
 *   ⛶   zoom (open the artifact in a fullscreen modal — owned by ArtifactColumn)
 *   ─   minimize (frontend-only hide, persisted to localStorage; the
 *       artifact stays in the DB and can be restored from the header)
 *   🗑️  delete — removes the registry row only. Workspace files stay where
 *       the agent wrote them; the user cleans those up from the workspace
 *       section in the config panel if they want. The confirm dialog spells
 *       this out so there's no surprise.
 *
 * Pin/unpin is intentionally NOT exposed: under the current LLM-driven flow
 * every agent-emitted artifact is auto-pinned at creation, and the route
 * refuses to unpin an artifact whose original_session_id is null (prevents
 * the limbo state). So the toggle has no working outcome in v1.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Minus, Trash2, Maximize2 } from 'lucide-react';
import { useArtifactStore } from '@/stores';
import type { Artifact } from '@/types/artifact';
import { Button, Dialog, DialogContent, DialogFooter } from '@/components/ui';

interface Props {
  agentId: string;
  /** Open the artifact in the fullscreen zoom modal. Owned by ArtifactColumn. */
  onZoom: (artifactId: string) => void;
}

export default function ArtifactTabStrip({ agentId, onZoom }: Props) {
  const { t } = useTranslation();
  const artifacts = useArtifactStore((s) => s.artifacts);
  const minimizedTabIds = useArtifactStore((s) => s.minimizedTabIds);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const setActive = useArtifactStore((s) => s.setActive);
  const minimizeTab = useArtifactStore((s) => s.minimizeTab);
  const deleteArtifact = useArtifactStore((s) => s.delete);

  const [deleteTarget, setDeleteTarget] = useState<Artifact | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const visible = artifacts.filter((a) => !minimizedTabIds.has(a.artifact_id));

  if (visible.length === 0 && !deleteTarget) {
    return <div className="text-xs opacity-50 px-3 py-2">{t('artifacts.tabStrip.noArtifacts')}</div>;
  }

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setSubmitting(true);
    try {
      await deleteArtifact(agentId, deleteTarget.artifact_id);
      setDeleteTarget(null);
    } catch (e) {
      window.alert(t('artifacts.tabStrip.deleteFailed', { error: String(e) }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="flex flex-row overflow-x-auto border-b border-[var(--border-default)]">
        {visible.map((a) => (
          <TabButton
            key={a.artifact_id}
            artifact={a}
            active={a.artifact_id === activeId}
            onClick={() => setActive(a.artifact_id)}
            onZoom={() => onZoom(a.artifact_id)}
            onMinimize={() => minimizeTab(a.artifact_id)}
            onDelete={() => setDeleteTarget(a)}
          />
        ))}
      </div>

      <Dialog
        isOpen={!!deleteTarget}
        onClose={() => !submitting && setDeleteTarget(null)}
        title={t('artifacts.tabStrip.deleteTitle')}
        size="md"
      >
        <DialogContent>
          <div className="text-sm text-[var(--text-secondary)] space-y-3">
            <p>
              {t('artifacts.tabStrip.deletePrompt')}{' '}
              <span className="font-semibold">&ldquo;{deleteTarget?.title}&rdquo;</span>?
            </p>
            <p className="text-xs opacity-80">
              {t('artifacts.tabStrip.deleteNote')}
            </p>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setDeleteTarget(null)} disabled={submitting}>
            {t('artifacts.tabStrip.cancel')}
          </Button>
          <Button variant="danger" onClick={handleDeleteConfirm} disabled={submitting}>
            {submitting ? t('artifacts.tabStrip.deleting') : t('artifacts.tabStrip.deleteTab')}
          </Button>
        </DialogFooter>
      </Dialog>
    </>
  );
}

function TabButton({
  artifact, active, onClick, onZoom, onMinimize, onDelete,
}: {
  artifact: Artifact;
  active: boolean;
  onClick: () => void;
  onZoom: () => void;
  onMinimize: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      onClick={onClick}
      onDoubleClick={(e) => { e.stopPropagation(); onZoom(); }}
      className={
        'flex items-center gap-2 px-3 py-2 cursor-pointer border-r border-[var(--border-default)] ' +
        (active ? 'bg-[var(--bg-primary)]' : 'opacity-70 hover:opacity-100')
      }
      title={t('artifacts.tabStrip.tabTitle')}
    >
      <span className="text-sm truncate max-w-[12rem]">{artifact.title}</span>
      <button
        onClick={(e) => { e.stopPropagation(); onZoom(); }}
        title={t('artifacts.tabStrip.zoomTitle')}
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors"
        aria-label={t('artifacts.zoom')}
      >
        <Maximize2 className="w-3.5 h-3.5" />
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onMinimize(); }}
        title={t('artifacts.tabStrip.minimizeTitle')}
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors"
        aria-label={t('artifacts.tabStrip.minimizeAria')}
      >
        <Minus className="w-3.5 h-3.5" />
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title={t('artifacts.tabStrip.deleteTabTitle')}
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-red-900/40 hover:text-red-400 transition-colors"
        aria-label={t('artifacts.tabStrip.deleteAria')}
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
