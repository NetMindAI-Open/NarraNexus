/**
 * @file_name: PrivacySettings.tsx
 * @description: Privacy pane — the two "what leaves this machine" switches.
 *
 * Two consents with deliberately different scopes, and the copy must not
 * blur them:
 *   - Product analytics: per-USER preference, persisted as a DB row
 *     (/api/auth/settings/analytics). Existed in the backend since 06-08
 *     but its only UI lived in the never-mounted SettingsModal — this
 *     pane is what finally makes it reachable.
 *   - Diagnostic telemetry: marker file, per user account on the host
 *     (/api/auth/settings/telemetry). `controllable=false` renders the
 *     toggle disabled with a note naming WHO owns it (managed_by:
 *     env override vs multi-tenant cloud) instead of hiding it — a
 *     switch that silently vanishes reads as "there is no telemetry",
 *     which would be false.
 *
 * The analytics toggle is optimistic with revert-on-error. The
 * telemetry toggle RECONCILES: after every PUT (success or failure) it
 * re-GETs the server state — the switch always lands on truth, never
 * on a client-fabricated guess, and a failed opt-out says so out loud.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, type TelemetryConsentState } from '@/lib/api';
import {
  isWebAnalyticsLoaded,
  markWebAnalyticsConsentRevoked,
} from '@/lib/analytics/webAnalytics';
import { Toggle } from '@/components/nm/form';

function ConsentRow({
  title,
  desc,
  note,
  checked,
  disabled,
  onChange,
}: {
  title: string;
  desc: string;
  note?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div
      className="flex items-start justify-between gap-4 py-4 border-b"
      style={{ borderColor: 'var(--nm-line)' }}
    >
      <div className="space-y-1 min-w-0">
        <div className="text-sm font-medium" style={{ color: 'var(--nm-ink)' }}>
          {title}
        </div>
        <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          {desc}
        </p>
        {note && (
          <p className="text-xs" style={{ color: 'var(--nm-ink50)' }}>
            {note}
          </p>
        )}
      </div>
      <Toggle
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        ariaLabel={title}
      />
    </div>
  );
}

export function PrivacySettings() {
  const { t } = useTranslation();

  const [analyticsEnabled, setAnalyticsEnabled] = useState<boolean | null>(null);
  const [analyticsBusy, setAnalyticsBusy] = useState(false);
  const [telemetry, setTelemetry] = useState<TelemetryConsentState | null>(null);
  const [telemetryBusy, setTelemetryBusy] = useState(false);
  // Fetch failure is its own state: rendering an unchecked switch when
  // the state is UNKNOWN would read as "telemetry is off" while it may
  // well be on and shipping — the one direction a privacy pane must
  // never err in.
  const [telemetryFailed, setTelemetryFailed] = useState(false);
  const [telemetryWriteError, setTelemetryWriteError] = useState(false);

  useEffect(() => {
    api
      .getAnalyticsOptOut()
      .then((optedOut) => setAnalyticsEnabled(!optedOut))
      .catch(() => setAnalyticsEnabled(true));
    api
      .getTelemetryConsent()
      .then(setTelemetry)
      .catch(() => setTelemetryFailed(true));
  }, []);

  const toggleAnalytics = useCallback(
    async (next: boolean) => {
      if (analyticsEnabled === null || analyticsBusy) return;
      setAnalyticsEnabled(next);
      setAnalyticsBusy(true);
      try {
        await api.setAnalyticsOptOut(!next);
        if (!next) {
          // Turning it OFF must take effect now. Close the in-flight
          // initWebAnalytics() window (it may be mid-await), then — only if GTM
          // actually loaded this page (desktop / local / dev / self-host never
          // do) — reload to shed it, since a script tag can't be un-loaded.
          markWebAnalyticsConsentRevoked();
          if (isWebAnalyticsLoaded()) window.location.reload();
        }
      } catch {
        setAnalyticsEnabled(!next); // revert on failure — do NOT reload
      } finally {
        setAnalyticsBusy(false);
      }
    },
    [analyticsEnabled, analyticsBusy],
  );

  const toggleTelemetry = useCallback(
    async (next: boolean) => {
      if (!telemetry || telemetryBusy || !telemetry.controllable) return;
      setTelemetryBusy(true);
      setTelemetryWriteError(false);
      try {
        await api.setTelemetryOptOut(!next);
      } catch {
        // A silently-failed opt-out is the worst failure a privacy
        // control can have — say so, and let the re-GET below land the
        // switch on the true state instead of a fabricated one.
        setTelemetryWriteError(true);
      }
      try {
        setTelemetry(await api.getTelemetryConsent());
      } catch {
        setTelemetry(null);
        setTelemetryFailed(true);
      }
      setTelemetryBusy(false);
    },
    [telemetry, telemetryBusy],
  );

  const telemetryNote = () => {
    if (telemetryWriteError) return t('pages.settings.privacy.telemetryError');
    if (!telemetry) return undefined;
    if (telemetry.managed_by === 'cloud')
      return t('pages.settings.privacy.telemetryManagedCloud');
    if (telemetry.managed_by === 'env')
      return t('pages.settings.privacy.telemetryManaged');
    return t('pages.settings.privacy.telemetryTiming');
  };

  return (
    <div>
      <ConsentRow
        title={t('pages.settings.privacy.analyticsTitle')}
        desc={t('pages.settings.privacy.analyticsDesc')}
        checked={analyticsEnabled ?? true}
        disabled={analyticsEnabled === null || analyticsBusy}
        onChange={toggleAnalytics}
      />
      {telemetryFailed ? (
        <div className="py-4 space-y-1">
          <div className="text-sm font-medium" style={{ color: 'var(--nm-ink)' }}>
            {t('pages.settings.privacy.telemetryTitle')}
          </div>
          <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
            {t('pages.settings.privacy.telemetryUnavailable')}
          </p>
        </div>
      ) : (
        <ConsentRow
          title={t('pages.settings.privacy.telemetryTitle')}
          desc={t('pages.settings.privacy.telemetryDesc')}
          note={telemetryNote()}
          checked={telemetry ? telemetry.mode !== 'off' : false}
          disabled={!telemetry || telemetryBusy || !telemetry.controllable}
          onChange={toggleTelemetry}
        />
      )}
    </div>
  );
}
