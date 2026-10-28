/**
 * @file_name: StepImport.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Welcome step 2 — bring the agents already on this machine in.
 * Renders the SAME [[ImportAgentPicker]] the sidebar modal renders, driven by
 * the same [[useAgentImport]] state; only the chrome differs (a flow footer
 * instead of dialog buttons).
 *
 * Reached only when detect actually found something — cloud and empty machines
 * never see this step at all ([[welcomeSteps]] drops it), so there is no empty
 * state to design here.
 *
 * The step advances on the batch report, not on the last apply: the user should
 * get to read what landed (and retry a failed row) before moving on.
 */

import { useTranslation } from 'react-i18next';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui';
import { ImportAgentPicker } from '@/components/layout/ImportAgentPicker';
import { useAgentImport } from '@/hooks';
import { WelcomeStepFrame } from './WelcomeStepFrame';
import type { FrameworkDetection, MigrationApplyResult } from '@/types';

export interface StepImportProps {
  /** Detections the page already probed — this step never re-scans on mount. */
  detections: FrameworkDetection[];
  /** Called with everything that landed, so the page can refresh the sidebar. */
  onDone: (results: MigrationApplyResult[]) => void;
  onSkip: () => void;
  onBack?: () => void;
}

export function StepImport({ detections, onDone, onSkip, onBack }: StepImportProps) {
  const { t } = useTranslation();
  const c = useAgentImport({ initialDetections: detections });

  const footerNote =
    c.phase === 'list'
      ? t('layout.importAgent.footerSelected', {
          count: c.selectedRows.length,
          sessions: c.selectedSessionTotal,
        })
      : c.phase === 'running'
        ? t('layout.importAgent.runningProgress', {
            done: c.batch.imported + c.batch.failed,
            total: c.progressRows.length,
          })
        : undefined;

  // list → import the checked rows; done → move on. While the queue runs the
  // only action offered is "stop after this one" (never mid-write).
  const primary =
    c.phase === 'list'
      ? {
          label: t('pages.welcome.import.importAndContinue', { count: c.selectedRows.length }),
          icon: <Download className="mr-1.5 h-4 w-4" />,
          onPress: () => c.startImport(c.selectedRows),
          disabled: c.selectedRows.length === 0,
        }
      : c.phase === 'done'
        ? {
            label: t('pages.welcome.continue'),
            icon: undefined,
            onPress: () => onDone(c.batch.results),
            disabled: false,
          }
        : undefined;

  return (
    <WelcomeStepFrame
      title={t('pages.welcome.import.title')}
      subtitle={t('pages.welcome.import.subtitle')}
      onBack={c.phase === 'list' ? onBack : undefined}
      footerNote={footerNote}
      primaryLabel={primary?.label}
      primaryIcon={primary?.icon}
      onPrimary={primary?.onPress}
      primaryDisabled={primary?.disabled}
      skipLabel={
        c.phase === 'running'
          ? c.stopping
            ? t('layout.importAgent.stopping')
            : t('layout.importAgent.stopAfterThis')
          : t('pages.welcome.import.skip')
      }
      onSkip={c.phase === 'running' ? c.requestStop : onSkip}
    >
      <ImportAgentPicker controller={c} lede={t('pages.welcome.import.lede')} />

      {c.phase === 'done' && c.skippedKeys.length > 0 && (
        <div className="mt-3 flex justify-center">
          <Button variant="ghost" size="sm" onClick={c.resumeSkipped}>
            {t('layout.importAgent.importRemaining', { count: c.skippedKeys.length })}
          </Button>
        </div>
      )}
    </WelcomeStepFrame>
  );
}
