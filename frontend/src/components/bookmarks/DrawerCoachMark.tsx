/**
 * @file_name: DrawerCoachMark.tsx
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: First-run banner inside the bookmark drawer.
 *
 * The artifacts panel opens pinned for a brand-new user — they have to SEE
 * where artifacts land before the panel is worth closing. The flip side is
 * that the same user has no idea how to get their screen back, so this one
 * -time banner names the two controls that do it: unpin (the panel becomes
 * a transient overlay) and close (reopen any panel from the chat header).
 * Dismissed once, it never returns; the caller owns that persistence.
 */

import { useTranslation } from 'react-i18next';
import { PinOff, X } from 'lucide-react';

interface DrawerCoachMarkProps {
  onDismiss: () => void;
}

export function DrawerCoachMark({ onDismiss }: DrawerCoachMarkProps) {
  const { t } = useTranslation();
  return (
    <div
      role="note"
      className="shrink-0 mx-3 mt-3 rounded-[var(--radius-md)] border border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/6 px-3 py-2.5"
      data-testid="drawer-coach-mark"
    >
      <div className="text-[12px] font-medium text-[var(--nm-ink)]">
        {t('bookmarks.coach.title')}
      </div>
      <div className="mt-1.5 space-y-1 text-[11px] leading-relaxed text-[var(--nm-ink70)]">
        <div className="flex items-start gap-1.5">
          <PinOff className="mt-px h-3 w-3 shrink-0" aria-hidden />
          <span>{t('bookmarks.coach.unpinHint')}</span>
        </div>
        <div className="flex items-start gap-1.5">
          <X className="mt-px h-3 w-3 shrink-0" aria-hidden />
          <span>{t('bookmarks.coach.closeHint')}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="mt-2 text-[11px] font-medium text-[var(--accent-primary)] hover:underline"
      >
        {t('bookmarks.coach.gotIt')}
      </button>
    </div>
  );
}
