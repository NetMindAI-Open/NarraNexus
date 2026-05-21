/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure:
 * ┌──────────┬──────────────────────┬──┬──────────────────┬──────────────────┐
 * │          │                      │║│                  │ [Tab] [Tab] [Bell]│
 * │  Agent   │      Chat Area       │║│ Artifact Column  ├──────────────────┤
 * │  List    │                      │║│ (auto-hides when │                  │
 * │          │  (Spacious chat)     │║│   no artifacts)  │  Context Panel   │
 * │          │                      │║│                  │  (Tab content)   │
 * └──────────┴──────────────────────┴──┴──────────────────┴──────────────────┘
 *                                    └─ Drag handle (chat ↔ artifacts)
 *
 * Right-side tabs: Runtime, Awareness, Agent Inbox, Jobs
 * Top-right bell: User Inbox Popover
 * Artifact column: auto-hides when no artifacts; collapses to sliver on demand.
 *
 * Chat ↔ Artifacts split is user-resizable via the ResizableDivider; ratio
 * persisted to localStorage. Divider hidden when the artifact column is in
 * sliver mode (no artifacts yet, or user-collapsed) because resizing a
 * 36-px sliver is meaningless.
 *
 * Signal source: artifact_id signals arrive via the chat WebSocket stream
 * (tool_output frames parsed in ChatPanel.tsx). loadPinned is called on mount /
 * agent change to hydrate agent-scoped artifacts. No dedicated artifact WS.
 */

import { useState, useEffect, useRef, useCallback, Suspense } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ContextPanelHeader, type ContextTab } from './ContextPanelHeader';
import { ContextPanelContent } from './ContextPanelContent';
import { ResizableDivider } from './ResizableDivider';
import { ChatPanel } from '@/components/chat';
import { OnboardingChecklist } from '@/components/onboarding/OnboardingChecklist';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { ArtifactColumn } from '@/components/artifacts';
import { useConfigStore, usePreloadStore, useArtifactStore } from '@/stores';
import { useAutoRefresh } from '@/hooks';

const SPLIT_STORAGE_KEY = 'chat_artifact_split_v1';
const DEFAULT_SPLIT = 0.6; // 60 % chat, 40 % artifacts — matches the legacy 3:2 flex shares.
const MIN_CHAT_PX = 400;
const MIN_ARTIFACT_PX = 320;
const SPLIT_HARD_MIN = 0.1;
const SPLIT_HARD_MAX = 0.9;

function readInitialSplit(): number {
  if (typeof window === 'undefined') return DEFAULT_SPLIT;
  const raw = window.localStorage.getItem(SPLIT_STORAGE_KEY);
  if (!raw) return DEFAULT_SPLIT;
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_SPLIT;
  return Math.min(SPLIT_HARD_MAX, Math.max(SPLIT_HARD_MIN, parsed));
}

/** Default chat view with context panel */
export function ChatView() {
  const [contextTab, setContextTab] = useState<ContextTab>('runtime');
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });

  const loadPinned = useArtifactStore((s) => s.loadPinned);
  const artifactsLength = useArtifactStore((s) => s.artifacts.length);
  const artifactsCollapsed = useArtifactStore((s) => s.collapsed);

  // Chat ↔ Artifacts split (fraction of joint width occupied by chat).
  //
  // Perf: dragging the divider does NOT resize the panes in real time —
  // resizing the artifact pane reflows whatever it hosts (an HTML
  // artifact is a sandboxed iframe; reflowing it every frame, especially
  // shrinking, is visibly janky). Instead, during the drag only a thin
  // "ghost" preview line moves (positioned imperatively, zero React
  // renders). On release `handleResizeEnd` commits the final ratio to
  // `chatSplit` state — exactly one re-render → one reflow per drag.
  const [chatSplit, setChatSplit] = useState<number>(() => readInitialSplit());
  const groupRef = useRef<HTMLDivElement | null>(null);
  const ghostLineRef = useRef<HTMLDivElement | null>(null);
  const pendingSplitRef = useRef<number>(chatSplit);

  // Persist on every committed change so refreshes preserve the layout.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(SPLIT_STORAGE_KEY, String(chatSplit));
  }, [chatSplit]);

  // Translate a raw pointer clientX into a clamped split fraction relative
  // to the joint chat+artifact area. Clamp tight against per-pane min
  // widths so neither pane can be dragged below its declared minimum.
  const computeSplit = useCallback((clientX: number): number | null => {
    const el = groupRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const rawRatio = (clientX - rect.left) / rect.width;
    const minRatio = Math.max(SPLIT_HARD_MIN, MIN_CHAT_PX / rect.width);
    const maxRatio = Math.min(SPLIT_HARD_MAX, 1 - MIN_ARTIFACT_PX / rect.width);
    // If the container is too narrow for both mins, just clamp to hard bounds.
    const lo = Math.min(minRatio, maxRatio);
    const hi = Math.max(minRatio, maxRatio);
    return Math.min(hi, Math.max(lo, rawRatio));
  }, []);

  // During drag (≤ once per frame): move the ghost preview line only. The
  // real columns stay put — no flex change, no iframe reflow.
  const handleResize = useCallback((clientX: number) => {
    const el = groupRef.current;
    const ghost = ghostLineRef.current;
    if (!el || !ghost) return;
    const split = computeSplit(clientX);
    if (split === null) return;
    pendingSplitRef.current = split;
    // Position the ghost at the *clamped* split so it previews exactly
    // where the panes will snap to on release.
    ghost.style.left = `${split * el.getBoundingClientRect().width}px`;
    ghost.style.display = 'block';
  }, [computeSplit]);

  // On release: hide the ghost, commit the final ratio to state (one
  // re-render → the columns resize and their content reflows once).
  const handleResizeEnd = useCallback((clientX: number) => {
    if (ghostLineRef.current) ghostLineRef.current.style.display = 'none';
    const split = computeSplit(clientX);
    if (split !== null) pendingSplitRef.current = split;
    setChatSplit(pendingSplitRef.current);
  }, [computeSplit]);

  // Load pinned artifacts whenever agentId changes.
  // Note: chatStore does not expose a per-agent session ID, so loadForSession
  // is not called here. Session-scoped artifacts arrive via the chat WS stream
  // (tool_output frames parsed in ChatPanel.tsx).
  // TODO: if chatStore gains a sessionId field, add loadForSession(agentId, sessionId) here.
  useEffect(() => {
    if (!agentId) return;
    loadPinned(agentId);
  }, [agentId, loadPinned]);

  // The divider only makes sense when the artifact column is in expanded
  // mode (has artifacts AND is not collapsed). In sliver mode the artifact
  // pane is a fixed 36-px button and a resize handle next to it would just
  // confuse the user.
  const artifactExpanded = !!agentId && artifactsLength > 0 && !artifactsCollapsed;

  return (
    <main className="flex-1 flex min-w-0 p-5 gap-5 overflow-hidden relative z-10">
      {/* Chat + Artifact group — owns the resizable divider + ghost line.
          `relative` so the ghost preview line can absolutely-position
          itself against this box. */}
      <div
        ref={groupRef}
        className="relative flex-[5] min-w-0 flex overflow-hidden"
      >
        {/* Chat column — NM paper card (the actual conversation surface,
            --nm-card sits on top of the warm nm-paper background).
            flex-col so the (cloud-only, self-hiding) onboarding checklist
            can sit above the chat without ChatPanel losing its height. */}
        <div
          className="min-w-[400px] animate-fade-in overflow-hidden rounded-[var(--radius-md)] flex flex-col"
          style={{
            background: 'var(--nm-card)',
            border: '1px solid var(--nm-hairline)',
            ...(artifactExpanded
              ? { flexGrow: chatSplit, flexBasis: 0 }
              : { flexGrow: 1, flexBasis: 0 }),
          }}
        >
          <OnboardingChecklist />
          <div className="flex-1 min-h-0">
            <ChatPanel onAgentComplete={refreshAll} />
          </div>
        </div>

        {/* Resizable divider (chat ↔ artifacts). Hidden in sliver mode.
            handleResize only moves the ghost line; handleResizeEnd
            commits the split to state once on release. */}
        {artifactExpanded && (
          <ResizableDivider onResize={handleResize} onResizeEnd={handleResizeEnd} />
        )}

        {/* Artifact column — auto-hides when no artifacts are loaded.
            flexGrow is passed only when the column is actually expanded so
            the sliver path keeps its fixed 36-px width. */}
        {agentId && (
          <ArtifactColumn
            agentId={agentId}
            flexGrow={artifactExpanded ? 1 - chatSplit : undefined}
          />
        )}

        {/* Drag preview line — only exists alongside the divider. Hidden
            (display:none) until handleResize toggles it on; positioned
            imperatively so the drag never triggers a React render. */}
        {artifactExpanded && (
          <div
            ref={ghostLineRef}
            className="absolute top-0 bottom-0 w-0.5 bg-[var(--text-primary)] pointer-events-none z-20 hidden"
            aria-hidden
          />
        )}
      </div>

      {/* Context column — NM paper-warm pane (sits beside the chat card,
          a half-shade warmer so it reads as "the sidebar belonging to
          this conversation"). */}
      <div
        className="flex-[2] min-w-[320px] flex flex-col animate-slide-in-right rounded-[var(--radius-md)] overflow-hidden"
        style={{
          background: 'var(--nm-paper-warm)',
          border: '1px solid var(--nm-hairline)',
          animationDelay: '0.1s',
        }}
      >
        <ContextPanelHeader
          activeTab={contextTab}
          onTabChange={setContextTab}
        />
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
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
