/**
 * @file_name: NoProviderNotice.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: A one-line strip at the top of the app for the user who skipped
 * the welcome flow's model step: no provider is wired, so their agent can't
 * answer. "Wire one now" jumps back to that step.
 *
 * This is the price of letting step 1 be skippable (Owner decision
 * 2026-08-27): a user without an API key must not be trapped on the first
 * screen, but they also must not be left wondering why nothing replies. The
 * strip is the honest middle — it names the cause instead of letting the user
 * discover it as a failed message.
 *
 * LOCAL ONLY: cloud accounts get a free-tier provider card at first login, so
 * there is nothing to warn about there.
 *
 * Dismissal is per-user localStorage: the user is allowed to say "I know". It
 * never re-arms itself — but it disappears on its own the moment a provider
 * exists, so the honest fix also clears it.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { TriangleAlert, X } from 'lucide-react';
import { api } from '@/lib/api';
import { useConfigStore, useRuntimeStore } from '@/stores';

const dismissKey = (userId: string) => `nn_no_provider_notice_dismissed:${userId}`;

const isDismissed = (userId: string): boolean => {
  try {
    return localStorage.getItem(dismissKey(userId)) === '1';
  } catch {
    return false;
  }
};

export function NoProviderNotice() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isLocal = useRuntimeStore((s) => s.mode) === 'local';
  const userId = useConfigStore((s) => s.userId);
  const [needsProvider, setNeedsProvider] = useState(false);
  const [dismissed, setDismissed] = useState(() => (userId ? isDismissed(userId) : true));

  // One probe per mount. A provider added later in this session isn't picked up
  // until the next load — acceptable for a hint, and cheaper than polling.
  useEffect(() => {
    if (!isLocal || !userId) return;
    let alive = true;
    api
      .getProviders()
      .then((res) => {
        if (!alive) return;
        const count = res.success && res.data?.providers ? Object.keys(res.data.providers).length : 0;
        setNeedsProvider(count === 0);
      })
      .catch(() => {
        /* backend not ready — say nothing rather than cry wolf */
      });
    return () => {
      alive = false;
    };
  }, [isLocal, userId]);

  if (!isLocal || !userId || !needsProvider || dismissed) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(dismissKey(userId), '1');
    } catch {
      /* non-fatal */
    }
    setDismissed(true);
  };

  return (
    <div
      role="status"
      className="flex shrink-0 items-center gap-2 border-b border-[color:var(--color-warning)] bg-[var(--color-warning)]/[0.07] px-4 py-2 text-xs text-[var(--color-warning)]"
    >
      <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{t('layout.noProvider.message')}</span>
      <button
        type="button"
        onClick={() => navigate('/welcome')}
        className="shrink-0 rounded-[var(--radius-xs)] border border-current px-2 py-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] hover:opacity-80"
      >
        {t('layout.noProvider.action')}
      </button>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t('common.close')}
        className="shrink-0 rounded-[var(--radius-xs)] p-0.5 hover:opacity-80"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
