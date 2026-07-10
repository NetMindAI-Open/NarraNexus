/**
 * @file_name: SettingsPage.tsx
 * @description: Settings page — NM section labels + display-font title.
 *
 * Reuses existing ProviderSettings, ArtifactsSection and adds bundle
 * export/import + batch agent manager links. Each section is headed with
 * a BracketSectionLabel so the page reads as a stack of NM-bracketed
 * regions instead of plain `<h2>` headings.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Package, Upload, Users, RefreshCw, CheckCircle2, AlertCircle, Download, Cpu, FolderArchive, CreditCard, SlidersHorizontal } from 'lucide-react';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { ModelDefaultsSettings } from '@/components/settings/ModelDefaultsSettings';
import { QuotaPanel } from '@/components/settings/QuotaPanel';
import { NetmindAccountPanel } from '@/components/settings/NetmindAccountPanel';
import ArtifactsSection from '@/components/settings/ArtifactsSection';
import { ScrollArea, Button } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { isTauri, kickUpdaterCheck, restartForUpdate } from '@/lib/tauri';
import { useUpdaterStore } from '@/stores/updaterStore';
import { useRuntimeStore } from '@/stores/runtimeStore';

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

function BundleContent() {
  const navigate = useNavigate();
  return (
    <section>
      <SectionHeader
        label="Bundle · Export / Import"
        hint="Package your agents (and optionally a team) into a portable .nxbundle file to share, or import a .nxbundle file shared with you."
      />
      <div className="flex gap-3">
        <Button onClick={() => navigate('/app/bundle/export')} className="gap-2">
          <Package className="w-4 h-4" />
          Export bundle…
        </Button>
        <Button onClick={() => navigate('/app/bundle/import')} variant="outline" className="gap-2">
          <Upload className="w-4 h-4" />
          Import bundle…
        </Button>
      </div>
    </section>
  );
}

function ArtifactsContent() {
  return (
    <section>
      <SectionHeader
        label="Artifacts"
        hint="Manage every chart, report, and file your agents have produced for you. Bulk-select to free up your quota when an agent reports it has hit the limit."
      />
      <ArtifactsSection />
    </section>
  );
}

function ManageAgentsContent() {
  const navigate = useNavigate();
  return (
    <section>
      <SectionHeader
        label="Manage agents · batch"
        hint="Bulk-select agents to delete, or batch-add/remove them from teams. Useful after importing a bundle you don't want to keep — filter by 'From bundles' to find them."
      />
      <Button onClick={() => navigate('/app/manage-agents')} variant="outline" className="gap-2">
        <Users className="w-4 h-4" />
        Open batch manager…
      </Button>
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

function UpdatesSection() {
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
          <div className="flex items-start gap-2 text-sm" style={{ color: 'var(--color-red-500)' }}>
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
function ProvidersSection() {
  return (
    <section>
      <SectionHeader label="LLM Providers" />
      {/* System free-tier quota now lives under Account & Subscription (all
          credits/billing in one place); this section is bring-your-own only. */}
      <ProviderSettings />
    </section>
  );
}

// Left-nav items (master). Each maps to one content panel (detail).
// ``desktopOnly`` items (App updates) only appear in the Tauri build.
// ``cloudOnly`` items (Account & Subscription) only appear in cloud-web —
// the account/billing panels are NetMind cloud features and render nothing
// locally, so the nav entry would otherwise open a blank pane.
interface NavItem {
  id: string;
  label: string;
  icon: typeof Cpu;
  desktopOnly?: boolean;
  cloudOnly?: boolean;
}

// Account first in cloud: a cloud user's home question is "what are my credits /
// plan", so billing leads; bring-your-own provider config follows.
const NAV_ITEMS: NavItem[] = [
  { id: 'account', label: 'Account & Subscription', icon: CreditCard, cloudOnly: true },
  { id: 'providers', label: 'LLM Providers', icon: Cpu },
  { id: 'modeldefaults', label: 'Model Defaults', icon: SlidersHorizontal },
  { id: 'bundle', label: 'Bundle', icon: Package },
  { id: 'artifacts', label: 'Artifacts', icon: FolderArchive },
  { id: 'agents', label: 'Manage agents', icon: Users },
  { id: 'updates', label: 'App updates', icon: Download, desktopOnly: true },
];

export default function SettingsPage() {
  const isCloud = useRuntimeStore((s) => s.mode) === 'cloud-web';
  const items = NAV_ITEMS.filter(
    (it) => (!it.desktopOnly || isTauri()) && (!it.cloudOnly || isCloud),
  );
  const [active, setActive] = useState(items[0]?.id ?? 'providers');

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-6 pb-4 shrink-0">
        <h1
          className="text-3xl font-bold tracking-tight"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          Settings
        </h1>
      </header>

      <div className="flex flex-1 min-h-0">
        {/* Left nav (master) */}
        <nav
          className="w-56 shrink-0 overflow-y-auto px-3 py-4 space-y-1 border-r"
          style={{ borderColor: 'var(--nm-line)' }}
        >
          {items.map((it) => {
            const Icon = it.icon;
            const isActive = active === it.id;
            return (
              <button
                key={it.id}
                type="button"
                onClick={() => setActive(it.id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-left transition-colors ${
                  isActive
                    ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] font-medium'
                    : 'text-[var(--nm-ink70)] hover:bg-[var(--nm-line)]/40 hover:text-[var(--nm-ink)]'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {it.label}
              </button>
            );
          })}
        </nav>

        {/* Content (detail) */}
        <ScrollArea className="flex-1" viewportClassName="p-6">
          <div className="max-w-3xl">
            {active === 'providers' && <ProvidersSection />}
            {active === 'modeldefaults' && (
              <section>
                <SectionHeader label="Model Defaults" hint="The framework + model every agent inherits by default. Per-agent overrides live in the chat page." />
                <ModelDefaultsSettings onManageProviders={() => setActive('providers')} />
              </section>
            )}
            {active === 'account' && (
              <section>
                <SectionHeader label="Account & Subscription" />
                {/* All "what are my credits / how is usage paid" concerns live
                    here: the platform free tier first, then the user's own
                    NetMind.AI Power balance/subscription/top-up. Both self-gate
                    to null when not applicable. */}
                <div className="mb-4">
                  <QuotaPanel />
                </div>
                <NetmindAccountPanel />
              </section>
            )}
            {active === 'bundle' && <BundleContent />}
            {active === 'artifacts' && <ArtifactsContent />}
            {active === 'agents' && <ManageAgentsContent />}
            {active === 'updates' && isTauri() && <UpdatesSection />}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}
