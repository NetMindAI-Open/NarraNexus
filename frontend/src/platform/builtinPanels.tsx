/**
 * @file_name: builtinPanels.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The shell's drawer panels as registrable components (one per rail tab).
 *
 * `BookmarkPanelHost` used to dispatch on the tab id with an `&&` chain;
 * each branch is now a tiny component here so `PANELS` can map an id to it
 * and a plugin can add a tab the same way. All heavy panels stay lazy.
 */
import { lazy } from 'react';
import { useStudioStore, selectStudioOpen } from '@/stores/studioStore';

import { useBookmarkStore } from '@/stores/bookmarkStore';
import type { PanelProps } from '@/platform/registries';

const BrowserStreamPanel = lazy(() => import('@/components/artifacts/renderers/BrowserStreamPanel'));

const AwarenessPanel = lazy(() =>
  import('@/components/awareness/AwarenessPanel').then((m) => ({ default: m.AwarenessPanel })),
);
const JobsPanel = lazy(() =>
  import('@/components/jobs/JobsPanel').then((m) => ({ default: m.JobsPanel })),
);
const AgentInboxPanel = lazy(() =>
  import('@/components/inbox/AgentInboxPanel').then((m) => ({ default: m.AgentInboxPanel })),
);
const SkillsPanel = lazy(() =>
  import('@/components/skills/SkillsPanel').then((m) => ({ default: m.SkillsPanel })),
);
const NarrativeList = lazy(() =>
  import('@/components/runtime/NarrativeList').then((m) => ({ default: m.NarrativeList })),
);
const ArtifactColumn = lazy(() =>
  import('@/components/artifacts').then((m) => ({ default: m.ArtifactColumn })),
);

const BuilderConfigPanel = lazy(() =>
  import('@/components/builder').then((m) => ({ default: m.BuilderConfigPanel })),
);

/** The creation studio's panel (dev #382): the pickable lists already hide the tab
 *  while the studio is closed, but a restored drawer tab or a deep link can still
 *  land here — a studio panel with no conversation driving it reads as broken. */
export function BuilderTab({ agentId }: PanelProps) {
  const studioOpen = useStudioStore(selectStudioOpen(agentId));
  if (!studioOpen) return null;
  return <BuilderConfigPanel agentId={agentId} />;
}

export function AwarenessTab() {
  return <AwarenessPanel embedded section="awareness" />;
}
export function WorkspaceTab() {
  return <AwarenessPanel embedded section="workspace" />;
}
export function ChannelsTab() {
  return <AwarenessPanel embedded section="channels" />;
}
export function SmartHomeTab() {
  return <AwarenessPanel embedded section="smarthome" />;
}
export function SocialTab() {
  return <AwarenessPanel embedded section="social" />;
}
export function JobsTab({ agentId }: PanelProps) {
  const handleJobResolved = (jobId: string) => {
    useBookmarkStore.getState().resolveJobAttention(agentId, jobId);
  };
  return <JobsPanel embedded onJobResolved={handleJobResolved} />;
}
export function InboxTab() {
  return <AgentInboxPanel embedded />;
}
/** Artifacts — the retired side column rendered as a drawer panel; the
 *  drawer shell owns visibility (the column's sliver/collapse logic is gone). */
export function ArtifactsTab({ agentId }: PanelProps) {
  return <ArtifactColumn agentId={agentId} />;
}
/** The live browser. Where the page the agent is driving actually shows up.
 *
 *  Until now the stream panel lived only inside `UrlRenderer`'s `stream`
 *  branch, which needs a URL artifact tab in that mode — so from the chat,
 *  where the user watches the agent work, it could not be reached at all.
 *  Addressed by AGENT id, the same key the stream route and the login
 *  notice use, so all three agree on which browser is meant. */
export function BrowserTab({ agentId }: PanelProps) {
  return <BrowserStreamPanel sessionId={agentId || null} />;
}
export function SkillsTab() {
  return <SkillsPanel embedded section="skills" />;
}
export function McpTab() {
  return <SkillsPanel embedded section="mcp" />;
}
export function MemoryTab() {
  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-1 py-2">
      <NarrativeList />
    </div>
  );
}
