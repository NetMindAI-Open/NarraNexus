/**
 * @file_name: TelemetryNotice.tsx
 * @description: One-time telemetry disclosure — the notice half of
 * notice-and-choice consent.
 *
 * The telemetry default flipped to ON in the same change that shipped
 * this notice (a default and its consent basis land together). The
 * banner shows once per browser profile (localStorage, HelpButton's
 * `_v1` key pattern, fail-closed on storage errors) and ONLY when
 * telemetry is actually active for this install — telling a user whose
 * deployment shipped `off` that "we now send logs" would be false.
 *
 * localStorage records "the notice was SHOWN", never consent itself:
 * consent state lives server-side in the marker file. A cleared browser
 * cache re-shows the notice; it cannot re-grant anything.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';

const DISCLOSURE_SEEN_KEY = 'telemetry_disclosure_seen_v1';

type NoticeVariant = 'controllable' | 'managed';

export function TelemetryNotice() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [variant, setVariant] = useState<NoticeVariant | null>(null);

  useEffect(() => {
    try {
      if (localStorage.getItem(DISCLOSURE_SEEN_KEY) === '1') return;
    } catch {
      return; // storage unavailable — fail closed, never nag every load
    }
    let cancelled = false;
    api
      .getTelemetryConsent()
      .then((state) => {
        if (cancelled || state.mode === 'off') return;
        // A notice that promises "turn it off in settings" to a user
        // whose switch is disabled would be false — managed installs
        // get their own wording and no settings button.
        setVariant(state.controllable ? 'controllable' : 'managed');
      })
      .catch(() => {
        /* consent state unknowable from here — a notice we cannot
           make truthful is worse than deferring it to the next load */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dismiss = useCallback(() => {
    try {
      localStorage.setItem(DISCLOSURE_SEEN_KEY, '1');
    } catch {
      /* non-fatal */
    }
    setVariant(null);
  }, []);

  if (!variant) return null;

  return (
    <div
      role="status"
      className="w-full rounded-xl border shadow-lg p-4 pointer-events-auto"
      style={{
        background: 'var(--nm-paper)',
        borderColor: 'var(--nm-line)',
        color: 'var(--nm-ink)',
      }}
    >
      <p className="text-sm" style={{ color: 'var(--nm-ink)' }}>
        {variant === 'managed'
          ? t('telemetryNotice.bodyManaged')
          : t('telemetryNotice.body')}
      </p>
      <div className="mt-3 flex justify-end gap-2">
        {variant === 'controllable' && (
          <button
            type="button"
            onClick={() => {
              dismiss();
              navigate('/app/settings?tab=privacy');
            }}
            className="px-3 py-1.5 rounded-lg text-sm border"
            style={{ borderColor: 'var(--nm-line)', color: 'var(--nm-ink70)' }}
          >
            {t('telemetryNotice.settings')}
          </button>
        )}
        <button
          type="button"
          onClick={dismiss}
          className="px-3 py-1.5 rounded-lg text-sm font-medium"
          style={{ background: 'var(--nm-ink)', color: 'var(--nm-paper)' }}
        >
          {t('telemetryNotice.dismiss')}
        </button>
      </div>
    </div>
  );
}
