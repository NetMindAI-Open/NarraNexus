/**
 * @file_name: WebAnalyticsNotice.tsx
 * @description: One-time disclosure that the cloud web version loads a
 * third-party web-analytics tag (Google Tag Manager) — the notice half of
 * notice-and-choice consent, mirroring TelemetryNotice.
 *
 * Shows once per browser profile (localStorage `_v1` key, fail-closed on
 * storage errors) and ONLY where GTM is actually active: a configured id
 * (official production host — see getWebAnalyticsConfig) AND the user not
 * opted out. Telling a user whose GTM never loads that "we send page data to
 * Google" would be false, so desktop / local / dev / self-host / opted-out
 * installs never see it.
 *
 * localStorage records "the notice was SHOWN", never consent — consent lives
 * server-side (the "Product analytics" opt-out this banner points to). Clearing
 * the cache re-shows the notice; it cannot re-grant anything.
 *
 * Layout: this renders a plain w-full card. MainLayout positions it (and
 * TelemetryNotice) inside one shared bottom-anchored flex-col slot so the two
 * cloud disclosures stack (never overlap) and the slot sizes itself.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { isTauri } from '@/lib/tauri';
import { getWebAnalyticsConfig } from '@/lib/runtimeConfig';

const DISCLOSURE_SEEN_KEY = 'web_analytics_disclosure_seen_v1';

export function WebAnalyticsNotice() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [show, setShow] = useState(false);

  useEffect(() => {
    // Re-derive the loader's user-facing gates synchronously (we can't use
    // isWebAnalyticsLoaded() — at mount the loader may still be mid-await). Must
    // stay a COMPLETE mirror of webAnalytics.ts's gates so the notice never
    // claims something false: never in the Tauri build, and only where an id is
    // configured (official host). Both are cheap and run before storage/network.
    if (isTauri()) return;
    if (!getWebAnalyticsConfig().gtmId) return;
    try {
      if (localStorage.getItem(DISCLOSURE_SEEN_KEY) === '1') return;
    } catch {
      return; // storage unavailable — fail closed, never nag every load
    }
    let cancelled = false;
    api
      .getAnalyticsOptOut()
      .then((optedOut) => {
        if (!cancelled && !optedOut) setShow(true);
      })
      .catch(() => {
        /* consent unknowable from here — a notice we cannot make truthful is
           worse than deferring it to the next load */
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
    setShow(false);
  }, []);

  if (!show) return null;

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
        {t('webAnalyticsNotice.body')}
      </p>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            dismiss();
            navigate('/app/settings?tab=privacy');
          }}
          className="px-3 py-1.5 rounded-lg text-sm border"
          style={{ borderColor: 'var(--nm-line)', color: 'var(--nm-ink70)' }}
        >
          {t('webAnalyticsNotice.settings')}
        </button>
        <button
          type="button"
          onClick={dismiss}
          className="px-3 py-1.5 rounded-lg text-sm font-medium"
          style={{ background: 'var(--nm-ink)', color: 'var(--nm-paper)' }}
        >
          {t('webAnalyticsNotice.dismiss')}
        </button>
      </div>
    </div>
  );
}
