/**
 * @file_name: HelpButton.tsx
 * @author:
 * @date: 2026-06-11
 * @description: The bottom-right "?" entry point for the help overlay.
 * Fixed-position circular button + the `?` keyboard shortcut (ignored
 * while typing in inputs). Owns the overlay open state.
 *
 * First-visit auto-open: a user who has never dismissed the guide gets
 * it automatically shortly after the page settles (Owner, 2026-06-11).
 * Dismissing it (got it / Esc / backdrop) marks it seen — manual
 * re-entry stays available via the ? button forever.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HelpCircle } from 'lucide-react';
import { HelpOverlay } from './HelpOverlay';
import type { HelpPage } from './helpContent';

interface HelpButtonProps {
  pages: HelpPage[];
}

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return (
    t.tagName === 'INPUT' ||
    t.tagName === 'TEXTAREA' ||
    t.isContentEditable
  );
}

const GUIDE_SEEN_KEY = 'help_guide_seen_v1';

export function HelpButton({ pages }: HelpButtonProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  // First visit → auto-open once the layout has settled (anchors need
  // their final positions). Missing anchors degrade gracefully, so a
  // brand-new user with no agent yet simply sees the setup notes.
  useEffect(() => {
    try {
      if (window.localStorage.getItem(GUIDE_SEEN_KEY) === '1') return;
    } catch {
      return; // storage unavailable — never auto-open
    }
    const t = window.setTimeout(() => setOpen(true), 700);
    return () => window.clearTimeout(t);
  }, []);

  const handleClose = () => {
    setOpen(false);
    try {
      window.localStorage.setItem(GUIDE_SEEN_KEY, '1');
    } catch { /* non-fatal */ }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '?' && !isTypingTarget(e.target)) {
        setOpen((v) => !v);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <button
        type="button"
        aria-label={t('help.button.explain')}
        title={t('help.button.explainTitle')}
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-4 right-4 z-[150] flex items-center justify-center w-8 h-8 rounded-full cursor-pointer transition-all duration-150 hover:scale-110"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          boxShadow: 'var(--nm-elev-1)',
          color: 'var(--nm-ink50)',
        }}
      >
        <HelpCircle className="w-4 h-4" aria-hidden />
      </button>
      <HelpOverlay open={open} pages={pages} onClose={handleClose} />
    </>
  );
}
