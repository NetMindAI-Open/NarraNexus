/**
 * @file_name: sections.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The builtin settings sections — one component per left-nav item.
 *
 * Extracted from SettingsPage so each pane is a registrable unit
 * (`pages/settings/registerBuiltinSections.ts` registers them into
 * SETTINGS_SECTIONS with a lazy import — `platform/builtin.ts` explicitly
 * does not, so this chunk stays lazy with the Settings page); SettingsPage
 * itself no longer knows which panes exist. Every section receives
 * `navigate(sectionId)` for cross-links.
 */
import { useTranslation } from 'react-i18next';
import { RefreshCw, CheckCircle2, AlertCircle, Download } from 'lucide-react';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { ModelDefaultsSettings } from '@/components/settings/ModelDefaultsSettings';
import { PluginsSettings } from '@/components/settings/PluginsSettings';
import { PluginFactory } from '@/components/settings/plugins';
import { PrivacySettings } from '@/components/settings/PrivacySettings';
import { PersonalizationSettings } from '@/components/settings/PersonalizationSettings';
import { NetmindAccountPanel } from '@/components/settings/NetmindAccountPanel';
import ArtifactsSection from '@/components/settings/ArtifactsSection';
import BrowserSettings from '@/components/settings/BrowserSettings';
import { Button } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { isTauri, kickUpdaterCheck, restartForUpdate } from '@/lib/tauri';
import { useUpdaterStore } from '@/stores/updaterStore';
import { useConfigStore } from '@/stores';
import { isForcedCloud } from '@/lib/runtimeConfig';
import type { SettingsSectionProps } from '@/platform/registries';

function SectionHeader({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="space-y-2 mb-3">
      <BracketSectionLabel>{label}</BracketSectionLabel>
      {hint && (
        <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          {hint}
        </p>
      )}
    </div>
  );
}

// Each settings area is now a nav-selected panel (master–detail) instead
// of a collapsible stack. One content component per nav item; the left nav
// in SettingsPage switches between them.

export function ArtifactsContent() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.artifacts.label')}
        hint={t('pages.settings.artifacts.hint')}
      />
      <ArtifactsSection />
    </section>
  );
}

// Desktop-only updates panel. Renders the live state of the unified
// updater state machine (Rust commands/updater.rs). All three entry
// points (startup auto, tray menu, this button) feed the same
// pipeline; this is just the most detailed surface — Settings shows
// each stage explicitly with a progress bar, while the global banner
// (App.tsx) only surfaces on Ready.
//
// State → UI:
//   idle / failed     → "Check for updates" button
//   checking          → spinner + "Checking GitHub…"
//   up_to_date        → ✓ "You're on vX (latest)" + small "Check again"
//   available         → spinner + "Update vY found, starting download"
//   downloading       → progress bar + bytes + percent
//   installing        → spinner + "Installing vY…"
//   ready             → ✓ "Update vY installed" + "Restart now" button
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function UpdatesSection() {
  const { t } = useTranslation();
  const state = useUpdaterStore((s) => s.state);

  const onCheck = async () => {
    // No local busy state — the store's `kind === 'checking'` is the
    // single source of truth, so the UI stays in sync whether the
    // pipeline was kicked from here, the tray, or startup.
    await kickUpdaterCheck();
  };

  const inFlight =
    state.kind === 'checking' ||
    state.kind === 'available' ||
    state.kind === 'downloading' ||
    state.kind === 'installing';

  return (
    <section>
      <SectionHeader
        label={t('pages.settings.updates.label')}
        hint={t('pages.settings.updates.hint')}
      />
      <div className="space-y-3">
        {/* Primary action row */}
        <div className="flex items-center gap-3">
          {state.kind === 'ready' ? (
            <Button onClick={() => restartForUpdate()} className="gap-2">
              <Download className="w-4 h-4" />
              {t('pages.settings.updates.restartToApply', { version: state.version })}
            </Button>
          ) : (
            <Button
              onClick={onCheck}
              disabled={inFlight}
              variant="outline"
              className="gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${inFlight ? 'animate-spin' : ''}`} />
              {state.kind === 'checking'
                ? t('pages.settings.updates.checking')
                : state.kind === 'available'
                  ? t('pages.settings.updates.updateFound', { version: state.version })
                  : state.kind === 'downloading'
                    ? t('pages.settings.updates.downloading')
                    : state.kind === 'installing'
                      ? t('pages.settings.updates.installing', { version: state.version })
                      : t('pages.settings.updates.checkForUpdates')}
            </Button>
          )}
        </div>

        {/* State-specific detail row */}
        {state.kind === 'up_to_date' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--nm-ink70)' }}>
            <CheckCircle2 className="w-4 h-4 text-[var(--accent-primary)]" />
            <span>
              {t('pages.settings.updates.upToDatePrefix')} <b>{state.current}</b> {t('pages.settings.updates.upToDateSuffix')}
            </span>
          </div>
        )}

        {state.kind === 'downloading' && (
          <div className="space-y-1.5 max-w-md">
            <div className="text-xs" style={{ color: 'var(--nm-ink70)' }}>
              {state.total != null
                ? t('pages.settings.updates.downloadProgress', {
                    downloaded: formatBytes(state.downloaded),
                    total: formatBytes(state.total),
                    percent: state.percent != null ? ` (${state.percent}%)` : '',
                  })
                : t('pages.settings.updates.downloadedBytes', {
                    downloaded: formatBytes(state.downloaded),
                  })}
            </div>
            <div
              className="w-full h-1.5 rounded-full overflow-hidden"
              style={{ backgroundColor: 'var(--nm-line)' }}
            >
              <div
                className="h-full transition-all duration-300"
                style={{
                  width: state.percent != null ? `${state.percent}%` : '20%',
                  backgroundColor: 'var(--accent-primary)',
                  // Indeterminate look when total unknown: subtle stripe
                  // animation. percent==null is the only branch that
                  // doesn't have its width tied to real progress, so we
                  // leave a 20% bar pulsing as "something is happening".
                }}
              />
            </div>
          </div>
        )}

        {state.kind === 'installing' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--nm-ink70)' }}>
            <RefreshCw className="w-4 h-4 animate-spin" />
            <span>{t('pages.settings.updates.installingDetail', { version: state.version })}</span>
          </div>
        )}

        {state.kind === 'ready' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--nm-ink70)' }}>
            <CheckCircle2 className="w-4 h-4 text-[var(--accent-primary)]" />
            <span>
              <b>{state.version}</b> {t('pages.settings.updates.readyDetail')}
            </span>
          </div>
        )}

        {state.kind === 'failed' && (
          <div className="flex items-start gap-2 text-sm" style={{ color: 'var(--color-error)' }}>
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <div>
              <div>{t('pages.settings.updates.failed', { stage: state.stage })}</div>
              <div className="text-xs opacity-80 mt-1 break-words">{state.error}</div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// Providers section. Per-agent model/framework selection moved into the chat
// page, so this page is a credential WALLET + a GLOBAL DEFAULT. ProviderSettings
// now owns the whole vertical flow — ① your providers (list), ② add a provider
// (one-key + CLI sign-in + custom + sync), ③ global default — so this wrapper is
// just the section header. (The old "Advanced" junk-drawer disclosure, the
// separate summary card, and the top-level one-key are gone — all folded into
// ProviderSettings' ordered sections.)
export function ProvidersSection() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader label={t('pages.settings.providers.label')} />
      {/* System free-tier quota now lives under Account & Subscription (all
          credits/billing in one place); this section is bring-your-own only. */}
      <ProviderSettings />
    </section>
  );
}

export function AccountSection() {
  const { t } = useTranslation();
  const netmindToken = useConfigStore((s) => s.netmindToken);
  return (
    <section>
      <SectionHeader label={t('pages.settings.nav.account')} />
      {/* The user-scoped account/billing/subscription surface —
          Stripe returns payers to ?tab=account (billing.py), which
          lands here with the nav intact. The panel self-gates to
          null without a NetMind session, so the hint keeps this
          pane from reading as blank. */}
      {netmindToken ? (
        <NetmindAccountPanel />
      ) : (
        <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          {t('pages.account.powerOnlyHint')}
        </p>
      )}
    </section>
  );
}

export function PersonalizationSection() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.personalization.label')}
        hint={t('pages.settings.personalization.hint')}
      />
      <PersonalizationSettings />
    </section>
  );
}

export function ModelDefaultsSection({ navigate }: SettingsSectionProps) {
  const { t } = useTranslation();
  // Cloud has no plugins pane (frameworks are pre-installed), so pass
  // undefined there — ModelDefaultsSettings then renders the pluginRequired
  // hint as PLAIN TEXT (its `? :` fallback) rather than a clickable-but-dead
  // link. (Only reachable in a cloud C1-failure state anyway; healthy cloud
  // never fires pluginRequired.)
  const isCloud = isForcedCloud();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.modelDefaults.label')}
        hint={t('pages.settings.modelDefaults.hint')}
      />
      <ModelDefaultsSettings
        onManageProviders={() => navigate('providers')}
        onManagePlugins={isCloud ? undefined : () => navigate('plugins')}
      />
    </section>
  );
}

export function PluginsSection() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.plugins.label')}
        hint={t('pages.settings.plugins.hint')}
      />
      <PluginsSettings />
      <div className="mt-6">
        <PluginFactory />
      </div>
    </section>
  );
}

export function PrivacySection() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.privacy.label')}
        hint={t('pages.settings.privacy.hint')}
      />
      <PrivacySettings />
    </section>
  );
}

// The agent's "no browser installed" refusal sends the user to this pane by
// name. It is registered on EVERY surface, not desktopOnly: the refusal can
// be seen wherever an agent runs, and a destination that exists only on some
// of them is the same dead end as no destination at all.
export function BrowserSection() {
  const { t } = useTranslation();
  return (
    <section>
      <SectionHeader
        label={t('pages.settings.browser.label')}
        hint={t('pages.settings.browser.hint')}
      />
      <BrowserSettings />
    </section>
  );
}

export function UpdatesSectionGuarded() {
  return isTauri() ? <UpdatesSection /> : null;
}
