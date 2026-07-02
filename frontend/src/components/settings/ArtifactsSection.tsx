/**
 * @file_name: ArtifactsSection.tsx
 * @description: Settings → Artifacts management panel.
 *
 * Lists every artifact owned by the current user across every agent the user
 * owns. Lets the user bulk-delete entries (registry rows; workspace files stay).
 *
 * Each row is read-only metadata (title, agent, kind, created_at, latest size).
 * Selection lives in local state, not the artifact store, because this view's
 * selection is short-lived (only meaningful while the user is curating the
 * list) and shouldn't survive a navigation away.
 */

import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Trash2, RefreshCw } from 'lucide-react';
import { Button, Dialog, DialogContent, DialogFooter } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { artifactsApi } from '@/services/artifactsApi';
import type { Artifact } from '@/types/artifact';

const KIND_LABEL: Record<string, string> = {
  'text/html': 'HTML',
  'application/vnd.echarts+json': 'Chart',
  'text/csv': 'CSV',
  'text/markdown': 'Markdown',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'application/pdf': 'PDF',
};

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso.slice(0, 10);
  const ms = Date.now() - then;
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function ArtifactsSection() {
  const { t } = useTranslation();
  const { userId } = useConfigStore();
  const [items, setItems] = useState<Artifact[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await artifactsApi.listAll(userId);
      setItems(list);
      setSelected((prev) => new Set([...prev].filter((id) => list.some((a) => a.artifact_id === id))));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const allSelected = items.length > 0 && selected.size === items.length;
  const noneSelected = selected.size === 0;

  const toggleAll = () => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(items.map((a) => a.artifact_id)));
  };

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openBulkDelete = () => {
    if (noneSelected) return;
    setConfirmOpen(true);
  };

  const handleBulkDelete = async () => {
    if (!userId || noneSelected) return;
    setSubmitting(true);
    try {
      await artifactsApi.bulkDelete(userId, Array.from(selected));
      setSelected(new Set());
      setConfirmOpen(false);
      await refresh();
    } catch (e) {
      window.alert(t('settings.artifacts.bulkDeleteFailed', { error: String(e) }));
    } finally {
      setSubmitting(false);
    }
  };

  if (!userId) {
    return <p className="text-sm text-[var(--text-secondary)]">{t('settings.artifacts.signIn')}</p>;
  }

  return (
    <div>
      {/* Bulk action bar */}
      <div className="mb-3 flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={toggleAll}
            disabled={items.length === 0}
          />
          {t('settings.artifacts.selectAll', { count: items.length })}
        </label>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            {t('settings.artifacts.refresh')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={noneSelected}
            onClick={openBulkDelete}
            className="gap-1 text-red-400 border-red-900/40 hover:bg-red-900/20"
          >
            <Trash2 className="w-3.5 h-3.5" />
            {t('settings.artifacts.deleteSelected', { count: selected.size })}
          </Button>
        </div>
      </div>

      <Dialog
        isOpen={confirmOpen}
        onClose={() => !submitting && setConfirmOpen(false)}
        title={selected.size === 1
          ? t('settings.artifacts.deleteDialogTitle', { count: selected.size })
          : t('settings.artifacts.deleteDialogTitlePlural', { count: selected.size })}
        size="md"
      >
        <DialogContent>
          <div className="text-sm text-[var(--text-secondary)] space-y-3">
            <p>
              {selected.size === 1
                ? t('settings.artifacts.removeOne')
                : t('settings.artifacts.removeMany', { count: selected.size })}
            </p>
            <p className="text-xs opacity-80">
              {t('settings.artifacts.removeNote')}
            </p>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setConfirmOpen(false)} disabled={submitting}>
            {t('settings.artifacts.cancel')}
          </Button>
          <Button variant="danger" onClick={handleBulkDelete} disabled={submitting}>
            {submitting ? t('settings.artifacts.deleting') : t('settings.artifacts.deleteN', { count: selected.size })}
          </Button>
        </DialogFooter>
      </Dialog>

      {/* List */}
      {error && (
        <div className="text-sm text-red-400 mb-2">{t('settings.artifacts.failedToLoad', { error })}</div>
      )}
      {!loading && items.length === 0 && !error && (
        <div className="text-sm text-[var(--text-secondary)] text-center py-8">
          {t('settings.artifacts.empty')}
        </div>
      )}
      {items.length > 0 && (
        <div className="border border-[var(--border-default)] divide-y divide-[var(--border-default)]">
          {items.map((a) => (
            <label
              key={a.artifact_id}
              className="flex items-center gap-3 px-3 py-2 hover:bg-[var(--bg-secondary)] cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selected.has(a.artifact_id)}
                onChange={() => toggleOne(a.artifact_id)}
                onClick={(e) => e.stopPropagation()}
              />
              <span className="flex-1 truncate" title={a.title}>{a.title}</span>
              <span className="text-xs text-[var(--text-secondary)] w-20 text-right">
                {KIND_LABEL[a.kind] ?? a.kind}
              </span>
              <span className="text-xs text-[var(--text-tertiary)] w-32 truncate" title={a.agent_id}>
                {t('settings.artifacts.agentPrefix', { id: a.agent_id.replace(/^agent_/, '').slice(0, 10) })}
              </span>
              <span className="text-xs text-[var(--text-tertiary)] w-20 text-right">
                {formatRelativeTime(a.updated_at)}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
