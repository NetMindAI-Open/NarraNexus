/**
 * @file_name: SettingsPage.tsx
 * @description: Settings page — NM section labels + display-font title.
 *
 * Reuses existing ProviderSettings, ArtifactsSection and adds bundle
 * export/import + batch agent manager links. Each section is headed with
 * a BracketSectionLabel so the page reads as a stack of NM-bracketed
 * regions instead of plain `<h2>` headings.
 */

import { useNavigate } from 'react-router-dom';
import { Package, Upload, Users, RefreshCw, CheckCircle2, AlertCircle, Download } from 'lucide-react';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import ArtifactsSection from '@/components/settings/ArtifactsSection';
import { ScrollArea, Button } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { isTauri, kickUpdaterCheck, restartForUpdate } from '@/lib/tauri';
import { useUpdaterStore } from '@/stores/updaterStore';

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
        label="App updates"
        hint="Check for and install the latest NarraNexus desktop build. Updates are signed, downloaded in the background, and apply on restart."
      />
      <div className="space-y-3">
        {/* Primary action row */}
        <div className="flex items-center gap-3">
          {state.kind === 'ready' ? (
            <Button onClick={() => restartForUpdate()} className="gap-2">
              <Download className="w-4 h-4" />
              Restart to apply {state.version}
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
                ? 'Checking…'
                : state.kind === 'available'
                  ? `Update ${state.version} found…`
                  : state.kind === 'downloading'
                    ? 'Downloading…'
                    : state.kind === 'installing'
                      ? `Installing ${state.version}…`
                      : 'Check for updates'}
            </Button>
          )}
        </div>

        {/* State-specific detail row */}
        {state.kind === 'up_to_date' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--nm-ink70)' }}>
            <CheckCircle2 className="w-4 h-4 text-[var(--accent-primary)]" />
            <span>
              You're on <b>{state.current}</b> — that's the latest version.
            </span>
          </div>
        )}

        {state.kind === 'downloading' && (
          <div className="space-y-1.5 max-w-md">
            <div className="text-xs" style={{ color: 'var(--nm-ink70)' }}>
              {state.total != null
                ? `${formatBytes(state.downloaded)} of ${formatBytes(state.total)}${
                    state.percent != null ? ` (${state.percent}%)` : ''
                  }`
                : `${formatBytes(state.downloaded)} downloaded`}
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
            <span>Installing {state.version} — almost done…</span>
          </div>
        )}

        {state.kind === 'ready' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--nm-ink70)' }}>
            <CheckCircle2 className="w-4 h-4 text-[var(--accent-primary)]" />
            <span>
              <b>{state.version}</b> downloaded — restart to finish.
            </span>
          </div>
        )}

        {state.kind === 'failed' && (
          <div className="flex items-start gap-2 text-sm" style={{ color: 'var(--color-red-500)' }}>
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <div>
              <div>Update {state.stage} failed.</div>
              <div className="text-xs opacity-80 mt-1 break-words">{state.error}</div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const navigate = useNavigate();
  return (
    <ScrollArea className="h-full" viewportClassName="p-6">
      <div className="max-w-4xl mx-auto space-y-8">
        <header>
          <h1
            className="text-3xl font-bold tracking-tight"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            Settings
          </h1>
          <div className="mt-2">
            <BracketSectionLabel>
              Providers · Bundle · Artifacts · Agents
            </BracketSectionLabel>
          </div>
        </header>

        <section>
          <SectionHeader label="LLM Providers" />
          <ProviderSettings />
        </section>

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

        <section>
          <SectionHeader
            label="Artifacts"
            hint="Manage every chart, report, and file your agents have produced for you. Bulk-select to free up your quota when an agent reports it has hit the limit."
          />
          <ArtifactsSection />
        </section>

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

        {isTauri() && <UpdatesSection />}
      </div>
    </ScrollArea>
  );
}
