/**
 * @file_name: ContextPanelContent.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Context Panel Content - Bioluminescent Terminal style
 *
 * Heavy panels are lazy-loaded on demand to avoid loading all code at first render (especially large libraries like ReactFlow / Markdown)
 */

import { lazy, Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import type { ContextTab } from './ContextPanelHeader';

const RuntimePanel = lazy(() => import('@/components/runtime/RuntimePanel'));
const AwarenessPanel = lazy(() => import('@/components/awareness/AwarenessPanel'));
const AgentInboxPanel = lazy(() => import('@/components/inbox/AgentInboxPanel'));
const JobsPanel = lazy(() => import('@/components/jobs/JobsPanel'));
const SkillsPanel = lazy(() => import('@/components/skills/SkillsPanel'));

function PanelFallback() {
  return (
    <div className="h-full flex items-center justify-center">
      <Loader2 className="w-6 h-6 text-[var(--accent-primary)] animate-spin" />
    </div>
  );
}

interface ContextPanelContentProps {
  activeTab: ContextTab;
}

export function ContextPanelContent({ activeTab }: ContextPanelContentProps) {
  return (
    <div className="flex-1 min-h-0 flex flex-col animate-fade-in" key={activeTab}>
      {/* `flex flex-col` is load-bearing: each panel is a `Card` sized with
          `h-full`, which only resolves against a parent that establishes a
          definite-height flex column. MainLayout's wrapper is already
          `flex flex-col`, but this intermediate div sat in between as a plain
          block — breaking the height chain so the panels' inner ScrollArea
          had no bounded height and clipped (instead of scrolled) overflow. */}
      <Suspense fallback={<PanelFallback />}>
        {activeTab === 'runtime' && <RuntimePanel />}
        {activeTab === 'awareness' && <AwarenessPanel />}
        {activeTab === 'inbox' && <AgentInboxPanel />}
        {activeTab === 'jobs' && <JobsPanel />}
        {activeTab === 'skills' && <SkillsPanel />}
      </Suspense>
    </div>
  );
}
