/**
 * @file_name: FeedbackButton.tsx
 * @author:
 * @date: 2026-07-10
 * @description: Floating feedback entry — the desktop home for the feedback
 * dialog (Owner moved it out of the sidebar footer, 2026-07-10).
 *
 * Placement: it shares HelpButton's circle + card visual language and stacks
 * directly above it when the "?" is on screen. The help button only renders
 * on the chat view, so on sub-pages (settings / system / dashboard) this
 * button drops down into the corner slot the "?" would have occupied —
 * hence the `aboveHelp` prop rather than a hardcoded offset.
 *
 * Mobile keeps its entry in the sidebar drawer footer instead: the bottom-
 * right corner there belongs to the composer, which is the same reason
 * HelpButton is desktop-only. Feedback must stay reachable everywhere, so
 * the two entries together cover every route on every viewport.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MessageSquarePlus } from 'lucide-react';
import { FeedbackDialog } from './FeedbackDialog';
import { cn } from '@/lib/utils';

interface FeedbackButtonProps {
  /** True when HelpButton's "?" occupies the bottom-right corner slot. */
  aboveHelp?: boolean;
}

export function FeedbackButton({ aboveHelp = false }: FeedbackButtonProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        aria-label={t('feedback.title')}
        title={t('feedback.title')}
        onClick={() => setOpen(true)}
        className={cn(
          'fixed right-4 z-[150] flex items-center justify-center w-8 h-8',
          'rounded-full cursor-pointer transition-all duration-150 hover:scale-110',
          aboveHelp ? 'bottom-14' : 'bottom-4',
        )}
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          boxShadow: 'var(--nm-elev-1)',
          color: 'var(--nm-ink50)',
        }}
      >
        <MessageSquarePlus className="w-4 h-4" aria-hidden />
      </button>
      <FeedbackDialog isOpen={open} onClose={() => setOpen(false)} />
    </>
  );
}
