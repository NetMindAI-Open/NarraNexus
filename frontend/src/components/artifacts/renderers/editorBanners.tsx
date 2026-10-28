/**
 * @file_name: editorBanners.tsx
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: Shared banner chrome for artifact editing surfaces — the 409
 * conflict two-choice and the draft-restored notice. Owned by neither editor:
 * ResidentTextEditor (csv/code) and MarkdownRenderer (block editor) render
 * the same states, and the wording must not drift between kinds.
 */

import { useTranslation } from 'react-i18next';

export function ConflictBanner({
  onOverwrite,
  onDiscard,
}: {
  onOverwrite: () => void;
  onDiscard: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="px-3 py-2 text-xs bg-red-500/10 border-b border-red-500/30 shrink-0 flex items-center gap-2">
      <span className="flex-1">{t('artifacts.editor.conflictBody')}</span>
      <button
        onClick={onOverwrite}
        className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)]"
      >
        {t('artifacts.editor.overwriteMine')}
      </button>
      <button
        onClick={onDiscard}
        className="px-2 py-0.5 border border-[var(--border-default)] hover:bg-[var(--nm-paper-warm)]"
      >
        {t('artifacts.editor.discardMine')}
      </button>
    </div>
  );
}

export function DraftUnavailableBanner() {
  const { t } = useTranslation();
  return (
    <div className="px-3 py-1.5 text-xs bg-red-500/10 border-b border-red-500/30 shrink-0">
      {t('artifacts.editor.draftUnavailable')}
    </div>
  );
}

export function DraftRestoredBanner() {
  const { t } = useTranslation();
  return (
    <div className="px-3 py-1.5 text-xs bg-amber-500/10 border-b border-amber-500/30 shrink-0">
      {t('artifacts.editor.draftRestored')}
    </div>
  );
}
