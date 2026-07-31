/**
 * @file_name: MigrationGuide.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: One-time guided flow that offers to import other-framework agents
 * on the local/desktop app. Replaces the old MigrationNudge banner.
 *
 * Flow (per-user, see lib/migrationGuide.ts):
 *   not welcomed → detect → if any agent found, show the welcome modal (once)
 *     [Import]   → mark welcomed + coachmarkPending → open ImportAgentModal
 *     [Later]/X  → mark welcomed + coachmarkPending → show the coach-mark
 *   welcomed & coachmarkPending & !coachmarkDone → coach-mark pointing at "+"
 *     (clicked away → coachmarkDone; a successful import also clears it)
 *
 * LOCAL ONLY: detect reads the user's filesystem (503s on cloud), so the whole
 * flow is gated on `mode === 'local'`.
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Download } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Button } from '@/components/ui';
import { useRuntimeStore, useConfigStore } from '@/stores';
import { useAgentImported } from '@/hooks';
import { api } from '@/lib/api';
import { frameworkLabel } from '@/lib/migrationLabels';
import {
  readMigrationGuide,
  writeMigrationGuide,
  type MigrationGuideState,
} from '@/lib/migrationGuide';
import type { FrameworkDetection, MigrationApplyResult } from '@/types';
import { ImportAgentModal } from '@/components/layout/ImportAgentModal';
import { MigrationCoachmark } from './MigrationCoachmark';

/** Gate: local + logged-in. Keying the inner on userId means its lazy state
 *  initializer reads the CORRECT user's persisted state exactly once. */
export function MigrationGuide() {
  const isLocal = useRuntimeStore((s) => s.mode) === 'local';
  const userId = useConfigStore((s) => s.userId);
  if (!isLocal || !userId) return null;
  return <MigrationGuideInner key={userId} userId={userId} />;
}

function MigrationGuideInner({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const onImported = useAgentImported();

  const [state, setState] = useState<MigrationGuideState>(() => readMigrationGuide(userId));
  const [detections, setDetections] = useState<FrameworkDetection[]>([]);
  const [importOpen, setImportOpen] = useState(false);

  const patch = (p: Partial<MigrationGuideState>) => setState(writeMigrationGuide(userId, p));

  // Detect only while not yet welcomed (setDetections runs in an async callback,
  // so this doesn't trip set-state-in-effect).
  useEffect(() => {
    if (state.welcomed) return;
    let alive = true;
    api
      .migrateDetect()
      .then((res) => { if (alive) setDetections(res.detections); })
      .catch(() => { /* best-effort; silent on failure */ });
    return () => { alive = false; };
  }, [state.welcomed]);

  // "Real" agents = exclude the global-shared-config fallback row.
  const real = detections.filter((d) => !d.signals.includes('global-shared-config'));
  const byFramework = Array.from(
    real.reduce((m, d) => m.set(d.framework, (m.get(d.framework) ?? 0) + 1), new Map<string, number>()),
  ).map(([fw, n]) => `${frameworkLabel(fw)} · ${n}`);

  const handleApplied = async (result: MigrationApplyResult) => {
    patch({ coachmarkDone: true }); // imported successfully → no need to point at "+"
    await onImported(result);
  };

  const showModal = !state.welcomed && real.length > 0 && !importOpen;
  const showCoachmark = state.welcomed && state.coachmarkPending && !state.coachmarkDone;
  // Later/X and Import both mark welcomed + arm the coach-mark, so bailing out of
  // the import modal still leaves the user pointed at where import lives.
  const armAndClose = () => patch({ welcomed: true, coachmarkPending: true });

  return (
    <>
      {showModal && (
        <Dialog
          isOpen
          onClose={armAndClose}
          title={t('onboarding.migrationGuide.title')}
          size="md"
        >
          <DialogContent>
            <div className="flex items-start gap-3 text-sm">
              <Download className="mt-0.5 h-5 w-5 shrink-0 text-[var(--nm-ink-soft)]" />
              <div>
                <p className="text-[var(--nm-ink)]">
                  {t('onboarding.migrationGuide.body', { count: real.length })}
                </p>
                <ul className="mt-2 space-y-0.5 text-xs text-[var(--nm-ink-soft)]">
                  {byFramework.map((line) => (
                    <li key={line}>· {line}</li>
                  ))}
                </ul>
              </div>
            </div>
          </DialogContent>
          <DialogFooter>
            <Button variant="ghost" onClick={armAndClose}>
              {t('onboarding.migrationGuide.later')}
            </Button>
            <Button onClick={() => { armAndClose(); setImportOpen(true); }}>
              {t('onboarding.migrationGuide.import')}
            </Button>
          </DialogFooter>
        </Dialog>
      )}

      {showCoachmark && (
        <MigrationCoachmark onDismiss={() => patch({ coachmarkDone: true })} />
      )}

      {importOpen && (
        <ImportAgentModal onClose={() => setImportOpen(false)} onApplied={handleApplied} />
      )}
    </>
  );
}
