/**
 * @file_name: MigrationCoachmark.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: A coach-mark bubble that points at the sidebar "+" (Create menu),
 * shown once after the user dismisses the migration welcome modal via Later/X.
 *
 * Rendering rides the shared AnchoredCoachmark (extracted 2026-08-19 when
 * GuideAgentCoachmark became a character-for-character copy of this file):
 * portal to body, anchor measuring with the 500ms mount-race interval, and
 * one-bubble-per-anchor queueing — on a local first run both this and the
 * guide coachmark can be armed at once, and they used to render overlapped
 * at identical fixed coordinates. This file owns only the copy; the gate
 * (local mode, post-modal) stays with MigrationGuide.
 */

import { useTranslation } from 'react-i18next';
import { AnchoredCoachmark } from '@/components/onboarding/AnchoredCoachmark';

const ANCHOR = '[data-help-id="sidebar.create-agent"]';

export function MigrationCoachmark({ onDismiss }: { onDismiss: () => void }) {
  const { t } = useTranslation();

  return (
    <AnchoredCoachmark
      anchorSelector={ANCHOR}
      onDismiss={onDismiss}
      dismissLabel={t('onboarding.migrationCoachmark.gotIt')}
    >
      {t('onboarding.migrationCoachmark.text', {
        action: t('layout.createMenu.importAgent'),
      })}
    </AnchoredCoachmark>
  );
}
