/**
 * @file_name: ImportAgentModal.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Dialog chrome around [[ImportAgentPicker]] — the sidebar "+"
 * entry point for "import agents from other tools on this machine".
 *
 * The body (grouped checkbox list, inline row detail, batch report) lives in
 * ImportAgentPicker and its state in [[useAgentImport]], because step 2 of the
 * first-run welcome flow ([[WelcomePage]]) shows the SAME picker as a full page.
 * What is left here is exactly what a dialog owns: the title per phase, the
 * footer buttons, and what closing means.
 *
 * Replaced the four-stage framework → source → preview → done wizard on
 * 2026-08-27 (Owner decision): it could only import ONE source per open, so a
 * machine with 26 Claude Code projects meant reopening the modal 26 times.
 *
 * LOCAL ONLY: detect/scan read the user's filesystem and 503 on cloud, so every
 * caller mounts this in local mode only.
 */

import { useTranslation } from 'react-i18next';
import { Download } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Button } from '@/components/ui';
import { useAgentImport } from '@/hooks/useAgentImport';
import { ImportAgentPicker } from './ImportAgentPicker';
import type { FrameworkDetection, MigrationApplyResult } from '@/types';

export interface ImportAgentModalProps {
  onClose: () => void;
  /** Post-import wiring (refresh the agent list, optionally open the agent). */
  onApplied: (results: MigrationApplyResult[], opts?: { open?: boolean }) => void;
  /** Optional intro line — a caller with its own framing supplies one. */
  lede?: string;
  /** Dismiss-button label; defaults to Cancel. */
  closeLabel?: string;
  /** Detections the caller already fetched — skips this modal's own detect. */
  initialDetections?: FrameworkDetection[];
}

export function ImportAgentModal({
  onClose,
  onApplied,
  lede,
  closeLabel,
  initialDetections,
}: ImportAgentModalProps) {
  const { t } = useTranslation();
  const c = useAgentImport({ initialDetections });

  const finish = (open: boolean) => {
    if (c.batch.results.length > 0) onApplied(c.batch.results, { open });
    onClose();
  };

  const title =
    c.phase === 'list'
      ? t('layout.importAgent.title')
      : c.phase === 'running'
        ? t('layout.importAgent.runningTitle')
        : t('layout.importAgent.doneTitle');

  // While the queue runs, X / Esc / backdrop mean "stop after this one" instead
  // of closing: unmounting mid-`/apply` would leave a half-populated agent and
  // strand the user with no idea what landed.
  return (
    <Dialog
      isOpen
      onClose={c.phase === 'running' ? c.requestStop : onClose}
      title={title}
      size="lg"
    >
      <DialogContent>
        <ImportAgentPicker controller={c} lede={lede} />
      </DialogContent>

      <DialogFooter className="justify-between">
        {c.phase === 'list' && (
          <>
            <span className="font-[family-name:var(--font-mono)] text-[11px] tabular-nums text-[var(--nm-ink50)]">
              {t('layout.importAgent.footerSelected', {
                count: c.selectedRows.length,
                sessions: c.selectedSessionTotal,
              })}
            </span>
            <span className="flex items-center gap-2">
              <Button variant="ghost" onClick={onClose}>
                {closeLabel ?? t('common.cancel')}
              </Button>
              <Button
                variant="accent"
                disabled={c.selectedRows.length === 0}
                onClick={() => c.startImport(c.selectedRows)}
              >
                <Download className="mr-1 h-3.5 w-3.5" />
                {t('layout.importAgent.importCount', { count: c.selectedRows.length })}
              </Button>
            </span>
          </>
        )}

        {c.phase === 'running' && (
          <>
            <span className="font-[family-name:var(--font-mono)] text-[11px] tabular-nums text-[var(--nm-ink50)]">
              {t('layout.importAgent.runningProgress', {
                done: c.batch.imported + c.batch.failed,
                total: c.progressRows.length,
              })}
            </span>
            <Button variant="ghost" onClick={c.requestStop} disabled={c.stopping}>
              {c.stopping
                ? t('layout.importAgent.stopping')
                : t('layout.importAgent.stopAfterThis')}
            </Button>
          </>
        )}

        {c.phase === 'done' && (
          <>
            <span className="flex items-center gap-2">
              {c.skippedKeys.length > 0 && (
                <Button variant="ghost" onClick={c.resumeSkipped}>
                  {t('layout.importAgent.importRemaining', { count: c.skippedKeys.length })}
                </Button>
              )}
            </span>
            <span className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => finish(false)}>
                {t('common.close')}
              </Button>
              {c.batch.results.length > 0 && (
                <Button variant="accent" onClick={() => finish(true)}>
                  {t('layout.importAgent.openNamed', { name: c.firstImportedName })}
                </Button>
              )}
            </span>
          </>
        )}
      </DialogFooter>
    </Dialog>
  );
}
