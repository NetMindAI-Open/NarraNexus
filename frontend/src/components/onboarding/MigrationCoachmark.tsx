/**
 * @file_name: MigrationCoachmark.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: A coach-mark bubble that points at the sidebar "+" (Create menu),
 * shown once after the user dismisses the migration welcome modal via Later/X.
 *
 * Anchors to the `data-help-id="sidebar.create-agent"` element the same way the
 * HelpOverlay does (querySelector + getBoundingClientRect), and portals to body
 * so it is never clipped by the sidebar scroll container. Stays until the user
 * clicks it away (no auto-fade). Expanded-sidebar only for now — if the anchor is
 * absent (collapsed rail) it simply renders nothing.
 */

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

const ANCHOR = '[data-help-id="sidebar.create-agent"]';

export function MigrationCoachmark({ onDismiss }: { onDismiss: () => void }) {
  const { t } = useTranslation();
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    let intervalId = 0;
    let attempts = 0;
    const stop = () => {
      if (intervalId) { window.clearInterval(intervalId); intervalId = 0; }
    };
    const measure = () => {
      const el = document.querySelector(ANCHOR) as HTMLElement | null;
      const r = el?.getBoundingClientRect() ?? null;
      // Ignore a zero-size / offscreen anchor (collapsed sidebar, not mounted yet).
      const next = r && r.width > 0 && r.height > 0 ? r : null;
      setRect((prev) => {
        // Skip a redundant render if the geometry is unchanged.
        if (prev && next && prev.top === next.top && prev.right === next.right) return prev;
        return next;
      });
      // The interval only wins the sidebar-mount race. Stop once we have the
      // anchor (resize keeps it fresh), or after ~10s if it never appears
      // (e.g. the sidebar stays collapsed) so we don't poll for the whole session.
      attempts += 1;
      if (next || attempts >= 20) stop();
    };
    measure();
    window.addEventListener('resize', measure);
    intervalId = window.setInterval(measure, 500);
    return () => {
      window.removeEventListener('resize', measure);
      stop();
    };
  }, []);

  if (!rect) return null;

  return createPortal(
    <div
      style={{
        position: 'fixed',
        top: rect.top + rect.height / 2,
        left: rect.right + 10,
        transform: 'translateY(-50%)',
        zIndex: 60,
        maxWidth: 240,
      }}
      className={cn(
        'rounded-[var(--radius-sm)] border shadow-md',
        'border-[var(--nm-ink)] bg-[var(--nm-paper)] px-3 py-2',
      )}
    >
      {/* left-pointing arrow toward the "+" */}
      <span
        className="absolute h-2 w-2 rotate-45 border-b border-l bg-[var(--nm-paper)] border-[var(--nm-ink)]"
        style={{ left: -5, top: '50%', marginTop: -4 }}
      />
      <div className="text-xs text-[var(--nm-ink)]">
        {t('onboarding.migrationCoachmark.text', {
          action: t('layout.createMenu.importAgent'),
        })}
      </div>
      <button
        onClick={onDismiss}
        className="mt-1.5 text-[11px] font-medium text-[var(--nm-ink)] underline"
      >
        {t('onboarding.migrationCoachmark.gotIt')}
      </button>
    </div>,
    document.body,
  );
}
