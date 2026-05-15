/**
 * @file_name: ArtifactsSection.tsx
 * @description: Settings → Artifacts management panel.
 *
 * Lists every artifact owned by the current user across every agent the user
 * owns. Surfaces the quota usage at the top (count + bytes, with the
 * deploy-mode-appropriate cap) and lets the user bulk-delete entries to free
 * the quota when they hit a "limit reached" prompt elsewhere in the app.
 *
 * Each row is read-only metadata (title, agent, kind, created_at, latest size).
 * Selection lives in local state, not the artifact store, because this view's
 * selection is short-lived (only meaningful while the user is curating the
 * list) and shouldn't survive a navigation away.
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { Trash2, RefreshCw } from 'lucide-react';
import { Button, Dialog, DialogContent, DialogFooter } from '@/components/ui';
import { useConfigStore } from '@/stores';
import {
  artifactsApi,
  type ArtifactQuotaInfo,
} from '@/services/artifactsApi';
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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

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
  const { userId } = useConfigStore();
  const [items, setItems] = useState<Artifact[]>([]);
  const [quota, setQuota] = useState<ArtifactQuotaInfo | null>(null);
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
      const [list, q] = await Promise.all([
        artifactsApi.listAll(userId),
        artifactsApi.getQuota(userId),
      ]);
      setItems(list);
      setQuota(q);
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
      window.alert(`Bulk delete failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  const usagePct = useMemo(() => {
    if (!quota || quota.count_limit === 0) return 0;
    return Math.min(100, Math.round((quota.used_count / quota.count_limit) * 100));
  }, [quota]);

  const usageColour =
    usagePct >= 100 ? 'bg-red-500'
    : usagePct >= 80 ? 'bg-amber-500'
    : 'bg-emerald-500';

  if (!userId) {
    return <p className="text-sm text-[var(--text-secondary)]">Sign in to manage artifacts.</p>;
  }

  return (
    <div>
      {/* Quota header */}
      {quota && (
        <div className="mb-4 p-3 border border-[var(--border-default)] bg-[var(--bg-secondary)]">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-medium">
              Artifacts: {quota.used_count} / {quota.count_limit}
              <span className="ml-2 text-xs text-[var(--text-secondary)]">
                ({formatBytes(quota.used_bytes)} / {formatBytes(quota.bytes_limit)})
              </span>
              <span className="ml-2 text-xs text-[var(--text-tertiary)]">
                · {quota.is_cloud_mode ? 'cloud limit' : 'local limit'}
              </span>
            </div>
            <Button variant="outline" size="sm" onClick={refresh} disabled={loading} className="gap-1">
              <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
          <div className="h-2 bg-[var(--bg-tertiary)] overflow-hidden">
            <div
              className={`h-full transition-all ${usageColour}`}
              style={{ width: `${usagePct}%` }}
            />
          </div>
          {usagePct >= 80 && (
            <p className="mt-2 text-xs text-amber-400">
              {usagePct >= 100
                ? "You've reached the limit. New artifacts will be rejected until you delete some here."
                : "You're approaching the limit — consider clearing old artifacts."}
            </p>
          )}
        </div>
      )}

      {/* Bulk action bar */}
      <div className="mb-3 flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={toggleAll}
            disabled={items.length === 0}
          />
          Select all ({items.length})
        </label>
        <Button
          variant="outline"
          size="sm"
          disabled={noneSelected}
          onClick={openBulkDelete}
          className="gap-1 text-red-400 border-red-900/40 hover:bg-red-900/20"
        >
          <Trash2 className="w-3.5 h-3.5" />
          Delete {selected.size} selected
        </Button>
      </div>

      <Dialog
        isOpen={confirmOpen}
        onClose={() => !submitting && setConfirmOpen(false)}
        title={`Delete ${selected.size} artifact${selected.size === 1 ? '' : 's'}`}
        size="md"
      >
        <DialogContent>
          <div className="text-sm text-[var(--text-secondary)] space-y-3">
            <p>
              {selected.size === 1
                ? 'Remove the selected artifact tab?'
                : `Remove the ${selected.size} selected artifact tabs?`}
            </p>
            <p className="text-xs opacity-80">
              Only the registry rows are removed. Each agent&rsquo;s workspace
              files stay where they were written — clean those up from the
              workspace section of each agent&rsquo;s config panel if you want
              to free disk space.
            </p>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setConfirmOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="danger" onClick={handleBulkDelete} disabled={submitting}>
            {submitting ? 'Deleting…' : `Delete ${selected.size}`}
          </Button>
        </DialogFooter>
      </Dialog>

      {/* List */}
      {error && (
        <div className="text-sm text-red-400 mb-2">Failed to load: {error}</div>
      )}
      {!loading && items.length === 0 && !error && (
        <div className="text-sm text-[var(--text-secondary)] text-center py-8">
          You don't have any artifacts yet. They'll appear here as agents create them.
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
                agent: {a.agent_id.replace(/^agent_/, '').slice(0, 10)}
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
