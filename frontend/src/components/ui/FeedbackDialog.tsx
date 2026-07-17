/**
 * FeedbackDialog — manual product-feedback entry (spec 2026-07-10).
 *
 * The explicit fallback channel next to the agent's automatic
 * submit_feedback tool: category select + free text, relayed by the
 * backend to the team's feedback intake. Fire-and-forget UX — we thank
 * the user even if the intake is unreachable (delivered=false).
 *
 * Layout follows the house pattern (see ConfirmDialog): Dialog's own body
 * has NO padding, so content must be wrapped in DialogContent and actions
 * in DialogFooter. Skipping them makes children bleed to the dialog edges.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter } from './Dialog';
import { Button } from './Button';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

const CATEGORIES = ['user_dissatisfaction', 'feature_gap', 'error', 'other'] as const;

const FIELD_CLASS =
  'w-full px-2.5 py-1.5 text-sm rounded-[var(--radius-md)] bg-transparent ' +
  'border border-[var(--nm-hairline)] text-[var(--nm-ink)] ' +
  'focus:outline-none focus:border-[var(--nm-ink50)] transition-colors';

interface FeedbackDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export function FeedbackDialog({ isOpen, onClose }: FeedbackDialogProps) {
  const { t } = useTranslation();
  const [category, setCategory] = useState<string>('other');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const handleClose = () => {
    setText('');
    setCategory('other');
    setDone(false);
    setBusy(false);
    onClose();
  };

  const handleSubmit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await api.submitFeedback(category, text.trim().slice(0, 500));
    } catch {
      // Feedback must never error at the user — treat as sent.
    }
    setDone(true);
    setBusy(false);
  };

  return (
    <Dialog isOpen={isOpen} onClose={handleClose} title={t('feedback.title')} size="md">
      {done ? (
        <>
          <DialogContent>
            <p className="text-sm text-[var(--text-secondary)]">{t('feedback.thanks')}</p>
          </DialogContent>
          <DialogFooter>
            <Button onClick={handleClose}>{t('feedback.close')}</Button>
          </DialogFooter>
        </>
      ) : (
        <>
          <DialogContent className="space-y-3">
            <div>
              <label className="block text-[11px] font-mono uppercase tracking-wider mb-1.5 text-[var(--text-secondary)]">
                {t('feedback.categoryLabel')}
              </label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className={FIELD_CLASS}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{t(`feedback.categories.${c}`)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-mono uppercase tracking-wider mb-1.5 text-[var(--text-secondary)]">
                {t('feedback.textLabel')}
              </label>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={500}
                rows={4}
                placeholder={t('feedback.placeholder')}
                // resize-none: dragging the handle past the dialog edge looks broken
                className={cn(FIELD_CLASS, 'resize-none leading-relaxed')}
              />
              <div className="mt-1 text-right text-[10px] font-mono text-[var(--text-tertiary)]">
                {text.length}/500
              </div>
            </div>
          </DialogContent>
          <DialogFooter>
            <Button variant="ghost" onClick={handleClose}>{t('feedback.cancel')}</Button>
            <Button onClick={handleSubmit} disabled={!text.trim() || busy}>
              {busy ? t('feedback.sending') : t('feedback.submit')}
            </Button>
          </DialogFooter>
        </>
      )}
    </Dialog>
  );
}
