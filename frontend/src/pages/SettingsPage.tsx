/**
 * @file_name: SettingsPage.tsx
 * @description: Settings page — NM section labels + display-font title.
 *
 * Reuses existing ProviderSettings, EmbeddingStatus, ArtifactsSection and
 * adds bundle export/import + batch agent manager links. Each section is
 * headed with a BracketSectionLabel so the page reads as a stack of
 * NM-bracketed regions instead of plain `<h2>` headings.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Package, Upload, Users, RefreshCw } from 'lucide-react';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import ArtifactsSection from '@/components/settings/ArtifactsSection';
import { EmbeddingStatus } from '@/components/ui/EmbeddingStatus';
import { ScrollArea, Button } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { isTauri, checkForUpdates } from '@/lib/tauri';

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

// Desktop-only: manual "Check for updates". The app also auto-checks on launch
// (Rust run_startup_update_check); this gives the user an explicit trigger.
function UpdatesSection() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const onCheck = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await checkForUpdates();
      if (r === 'up_to_date') setMsg("You're on the latest version.");
      else if (r && r.startsWith('installed:'))
        setMsg(`Update ${r.slice('installed:'.length)} installed — restart to apply.`);
      else setMsg('Update check complete.');
    } catch (e) {
      setMsg(`Update check failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section>
      <SectionHeader
        label="App updates"
        hint="Check for and install the latest NarraNexus desktop build. Updates are signed and apply on restart."
      />
      <div className="flex items-center gap-3">
        <Button onClick={onCheck} disabled={busy} variant="outline" className="gap-2">
          <RefreshCw className={`w-4 h-4 ${busy ? 'animate-spin' : ''}`} />
          {busy ? 'Checking…' : 'Check for updates'}
        </Button>
        {msg && (
          <span className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
            {msg}
          </span>
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
              Providers · Embedding · Bundle · Artifacts · Agents
            </BracketSectionLabel>
          </div>
        </header>

        <section>
          <SectionHeader label="LLM Providers" />
          <ProviderSettings />
        </section>

        <section>
          <SectionHeader label="Embedding Index" />
          <EmbeddingStatus />
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
