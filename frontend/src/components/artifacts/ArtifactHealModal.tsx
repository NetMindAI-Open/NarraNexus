/**
 * @file_name: ArtifactHealModal.tsx
 * @description: Recover-broken-pointer modal.
 *
 * Shown by an artifact renderer (ChartRenderer / HtmlRenderer / ...) when
 * the raw-content fetch returns 410 — meaning the row's file_path is empty
 * in the DB or the file is missing on disk. The backend's /heal endpoint
 * has already been called and returned a list of candidate workspace files
 * matching the artifact's kind; this modal lets the user pick one to
 * re-register onto the same artifact_id.
 *
 * Once the user picks, we call /heal again with entry_path set; on success
 * the parent renderer triggers a reload of the artifact data, which lands
 * on a now-valid pointer.
 *
 * If the candidates list is empty, the modal shows a "couldn't find any
 * matching file" state with a link the user can follow to regenerate the
 * artifact (re-run the agent) — there is nothing to pick.
 */

import { useState } from 'react';
import { AlertTriangle, FileWarning, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui';
import type { HealCandidate } from '@/services/artifactsApi';

interface Props {
  open: boolean;
  artifactTitle: string;
  candidates: HealCandidate[];
  message: string;
  busy: boolean;
  onPick: (workspacePath: string) => void;
  onDismiss: () => void;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatMtime(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString();
}

export default function ArtifactHealModal({
  open,
  artifactTitle,
  candidates,
  message,
  busy,
  onPick,
  onDismiss,
}: Props) {
  const [picked, setPicked] = useState<string | null>(null);
  if (!open) return null;

  const empty = candidates.length === 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm p-4"
      style={{ background: 'var(--nm-backdrop, rgba(0,0,0,0.5))' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="heal-modal-title"
    >
      <div className="w-full max-w-2xl border border-[var(--border-default)] bg-[var(--bg-primary)] p-5 shadow-2xl">
        <div className="flex items-start gap-3 mb-3">
          {empty ? (
            <FileWarning className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
          )}
          <div className="flex-1">
            <h2 id="heal-modal-title" className="text-base font-semibold mb-1">
              Recover artifact: {artifactTitle}
            </h2>
            <p className="text-sm text-[var(--text-secondary)] whitespace-pre-line">
              {message}
            </p>
          </div>
        </div>

        {!empty && (
          <div className="border border-[var(--border-default)] divide-y divide-[var(--border-default)] max-h-72 overflow-y-auto mb-4">
            {candidates.map((c) => (
              <label
                key={c.workspace_path}
                className="flex items-center gap-3 px-3 py-2 cursor-pointer text-sm hover:bg-[var(--bg-secondary)]"
              >
                <input
                  type="radio"
                  name="heal-candidate"
                  checked={picked === c.workspace_path}
                  onChange={() => setPicked(c.workspace_path)}
                  disabled={busy}
                />
                <span className="flex-1 truncate font-mono text-xs" title={c.workspace_path}>
                  {c.workspace_path}
                </span>
                <span className="w-20 text-right text-xs text-[var(--text-tertiary)]">
                  {formatBytes(c.size_bytes)}
                </span>
                <span className="w-40 text-right text-xs text-[var(--text-tertiary)]">
                  {formatMtime(c.mtime)}
                </span>
              </label>
            ))}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onDismiss} disabled={busy}>
            Dismiss
          </Button>
          {!empty && (
            <Button
              onClick={() => picked && onPick(picked)}
              disabled={busy || picked === null}
            >
              {busy ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> Registering…
                </span>
              ) : (
                'Register selected'
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
