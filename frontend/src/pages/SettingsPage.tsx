/**
 * @file_name: SettingsPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Settings page — reuses existing ProviderSettings + adds mode switching
 *
 * Uses the existing ProviderSettings component (which calls /api/providers)
 * for LLM configuration, and adds a mode switch section for local/cloud toggle.
 */

import { useNavigate } from 'react-router-dom';
import { Package, Upload, Users } from 'lucide-react';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import ArtifactsSection from '@/components/settings/ArtifactsSection';
import { EmbeddingStatus } from '@/components/ui/EmbeddingStatus';
import { ScrollArea, Button } from '@/components/ui';

export default function SettingsPage() {
  const navigate = useNavigate();
  return (
    <ScrollArea className="h-full" viewportClassName="p-6">
      <div className="space-y-6">
      {/* LLM Provider Configuration — uses existing component that calls /api/providers */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          LLM Providers
        </h2>
        <ProviderSettings />
      </section>

      {/* Embedding Status */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Embedding Index
        </h2>
        <EmbeddingStatus />
      </section>

      {/* Bundle Export / Import — Subproject 2 */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Bundle (export / import agents)
        </h2>
        <p className="text-sm text-[var(--text-secondary)] mb-3">
          Package your agents (and optionally a team) into a portable .nxbundle file
          to share with someone else, or import a .nxbundle file shared with you.
        </p>
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

      {/* Artifact management — Phase 1 of agent-artifact-tabs */}
      <section>
        <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
          Artifacts
        </h2>
        <p className="text-sm text-[var(--text-secondary)] mb-3">
          Manage every chart, report, and file your agents have produced for you.
          Bulk-select to free up your quota when an agent reports it has hit the limit.
        </p>
        <ArtifactsSection />
      </section>

      {/* Batch agent management — issue 8.B replacement for "undo import" */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Manage agents (batch)
        </h2>
        <p className="text-sm text-[var(--text-secondary)] mb-3">
          Bulk-select agents to delete, or batch-add/remove them from teams.
          Useful after importing a bundle you don't want to keep — filter by
          "From bundles" to find them.
        </p>
        <Button onClick={() => navigate('/app/manage-agents')} variant="outline" className="gap-2">
          <Users className="w-4 h-4" />
          Open batch manager…
        </Button>
      </section>
      </div>
    </ScrollArea>
  );
}
