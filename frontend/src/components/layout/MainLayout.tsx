/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure:
 * ┌──────────┬─────────────────────────────┬──────────────────┐
 * │          │                             │ [Tab] [Tab] [Bell]│
 * │  Agent   │        Chat Area            ├──────────────────┤
 * │  List    │                             │                  │
 * │          │     (Spacious chat area)    │  Context Panel   │
 * │          │                             │  (Tab content)   │
 * └──────────┴─────────────────────────────┴──────────────────┘
 *
 * Right-side tabs: Runtime, Awareness, Agent Inbox, Jobs
 * Top-right bell: User Inbox Popover
 */

import { useState, useEffect, Suspense } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ContextPanelHeader, type ContextTab } from './ContextPanelHeader';
import { ContextPanelContent } from './ContextPanelContent';
import { ChatPanel } from '@/components/chat';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { useConfigStore, usePreloadStore } from '@/stores';
import { useAutoRefresh } from '@/hooks';

/** Default chat view with context panel */
export function ChatView() {
  const [contextTab, setContextTab] = useState<ContextTab>('runtime');
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });

  return (
    <main className="flex-1 flex min-w-0 p-5 gap-5 overflow-hidden relative z-10">
      {/* Chat column — outer border gives the column a single frame */}
      <div className="flex-[3] min-w-[400px] animate-fade-in border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
        <ChatPanel onAgentComplete={refreshAll} />
      </div>

      {/* Context column */}
      <div
        className="flex-[2] min-w-[320px] flex flex-col animate-slide-in-right"
        style={{ animationDelay: '0.1s' }}
      >
        <ContextPanelHeader
          activeTab={contextTab}
          onTabChange={setContextTab}
        />
        <div className="flex-1 min-h-0 flex flex-col border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
          <ContextPanelContent activeTab={contextTab} />
        </div>
      </div>
    </main>
  );
}

export function MainLayout() {
  const { agentId, userId } = useConfigStore();
  const { preloadAll } = usePreloadStore();
  const location = useLocation();

  // Check if we are rendering a sub-page (system, settings) vs. the chat view
  const isSubPage = location.pathname !== '/app/chat' && location.pathname !== '/app';

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  return (
    <div className="h-screen flex bg-[var(--bg-deep)] relative overflow-hidden">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Background agent completion toasts */}
      <AgentCompletionToast />

      {/* Render sub-page via Outlet, or the default chat view */}
      {isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          {/* v2.2 G1: inner Suspense so lazy sub-pages (DashboardPage etc.)
              don't trigger the App-level full-screen spinner that hides the
              Sidebar. The skeleton mirrors the dashboard grid shape. */}
          <Suspense fallback={<DashboardSkeleton />}>
            <Outlet />
          </Suspense>
        </main>
      ) : (
        <ChatView />
      )}
    </div>
  );
}

export default MainLayout;
