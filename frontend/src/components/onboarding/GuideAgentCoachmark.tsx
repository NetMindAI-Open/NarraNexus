/**
 * @file_name: GuideAgentCoachmark.tsx
 * @author: Bin Liang
 * @date: 2026-08-19
 * @description: A one-shot coach-mark bubble for brand-new users, pointing at
 * the sidebar "+" (Create menu): their first agent (the onboarding guide) was
 * auto-created server-side, so this is the nudge that they can create more
 * themselves. Shown while lib/guideCoachmark reports 'pending' (set by the
 * login path when the backend says is_new_user AND the deployment's guide
 * provisioning is on); clicking it away writes 'done' and it never returns.
 *
 * Rendering rides the shared AnchoredCoachmark (portal, anchor measuring,
 * one-bubble-per-anchor queueing with MigrationCoachmark); this file owns
 * only the gate and the copy.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AnchoredCoachmark } from '@/components/onboarding/AnchoredCoachmark';
import {
  dismissGuideCoachmark,
  isGuideCoachmarkPending,
} from '@/lib/guideCoachmark';

const ANCHOR = '[data-help-id="sidebar.create-agent"]';

export function GuideAgentCoachmark() {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(() => isGuideCoachmarkPending());

  if (!visible) return null;

  return (
    <AnchoredCoachmark
      anchorSelector={ANCHOR}
      onDismiss={() => {
        dismissGuideCoachmark();
        setVisible(false);
      }}
      dismissLabel={t('onboarding.guideCoachmark.gotIt')}
    >
      {t('onboarding.guideCoachmark.text')}
    </AnchoredCoachmark>
  );
}
