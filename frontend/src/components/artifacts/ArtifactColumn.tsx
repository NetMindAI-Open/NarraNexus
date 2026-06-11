/**
 * @file_name: ArtifactColumn.tsx
 * @description: The 4th column in the agent layout. Hosts ArtifactTabStrip plus a
 * lazy-rendered content area that dispatches to the appropriate renderer by
 * artifact kind. Collapses to a sliver button when the user dismisses it;
 * also renders as a sliver (never fully hidden) when no artifacts exist yet,
 * so the user always knows the panel is there. Auto-expands the moment a
 * new artifact arrives.
 *
 * Renderer dispatch is delegated to ArtifactRenderer (so the zoom modal can
 * reuse the same lazy-loaded chunks).
 *
 * Each tab now has a "zoom" affordance — clicking it pops the artifact into
 * a fullscreen modal (ArtifactZoomModal) with a blurred backdrop.
 */

import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, Maximize2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useArtifactStore } from '@/stores';
import ArtifactTabStrip from './ArtifactTabStrip';
import ArtifactDownloadMenu from './ArtifactDownloadMenu';
import ArtifactRenderer from './ArtifactRenderer';
import ArtifactZoomModal from './ArtifactZoomModal';

interface Props {
  agentId: string;
  /**
   * Optional flex-grow override (used in expanded mode only). The parent
   * layout passes this to drive the chat ↔ artifacts split via the
   * ResizableDivider. When omitted, falls back to the legacy `flex-[2]`
   * proportion. Sliver mode always uses the fixed 36 px width.
   */
  flexGrow?: number;
}

export default function ArtifactColumn({ agentId, flexGrow }: Props) {
  // All hooks must run in the same order on every render — no conditional hook
  // calls. Selectors first, then early returns.
  const artifacts = useArtifactStore((s) => s.artifacts);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const collapsed = useArtifactStore((s) => s.collapsed);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);
  const minimizedTabIds = useArtifactStore((s) => s.minimizedTabIds);
  const restoreTab = useArtifactStore((s) => s.restoreTab);
  const loadPinned = useArtifactStore((s) => s.loadPinned);
  const chartLruOrder = useArtifactStore((s) => s.chartLruOrder);

  const [zoomedId, setZoomedId] = useState<string | null>(null);
  // Manual refresh: artifacts are intentionally NOT polled on a timer
  // (event-driven — agent-complete reload + mid-stream tool_output
  // discovery cover the real cases). This button is the escape hatch for
  // when the user wants to force a re-sync anyway.
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await loadPinned(agentId);
    } finally {
      setRefreshing(false);
    }
  };

  // Auto-expand on artifact arrival.
  //
  // Pre-2026-05-13 behaviour: the column was unmounted entirely while
  // `artifacts.length === 0`, then suddenly popped into existence when
  // the first artifact landed — felt abrupt and a beat slow because the
  // user wasn't aware the panel existed. New behaviour: always render
  // the sliver (even with 0 artifacts) so the panel has a visual
  // presence from day one, and auto-uncollapse it the moment a new
  // artifact arrives so the user doesn't have to click the sliver to
  // discover the freshly-created artifact.
  //
  // We track the previous length in a ref. Mount initialises prev =
  // current, so first paint with cached artifacts (stale-while-
  // revalidate after agent switch) does NOT auto-expand — only genuine
  // growth past the previous tick triggers expansion. The "fights the
  // user who just collapsed" edge case is accepted as a tradeoff —
  // user explicitly asked for new-artifact-pops-it-open semantics
  // (they can re-collapse, and the next growth event will pop it open
  // again, which matches "tell me when something new is here").
  const prevLengthRef = useRef(artifacts.length);
  useEffect(() => {
    if (artifacts.length > prevLengthRef.current && collapsed) {
      setCollapsed(false);
    }
    prevLengthRef.current = artifacts.length;
  }, [artifacts.length, collapsed, setCollapsed]);

  // Sliver form: shown when the user collapsed the column OR when no
  // artifacts exist yet. The empty-state sliver advertises the panel's
  // existence so users know where artifacts will appear once the agent
  // creates one.
  const effectiveCollapsed = collapsed || artifacts.length === 0;
  if (effectiveCollapsed) {
    const hasArtifacts = artifacts.length > 0;
    // A <div> wrapper (not a single <button>) so the sliver can hold TWO
    // controls without nesting buttons: the expand affordance and a
    // refresh button. The refresh button matters most in the empty state
    // — that's exactly when the user wants to force a re-sync but the
    // expanded-header refresh button isn't reachable.
    return (
      <div className="w-9 border border-[var(--border-default)] bg-[var(--bg-primary)] flex flex-col items-center pt-3 pb-2 gap-2">
        <button
          onClick={() => setCollapsed(false)}
          className="flex-1 flex flex-col items-center group hover:bg-[var(--bg-secondary)] transition-colors w-full"
          title={
            hasArtifacts
              ? `Click to expand · ${artifacts.length} artifact${artifacts.length === 1 ? '' : 's'}`
              : 'Artifacts will appear here once the agent creates one'
          }
          aria-label={
            hasArtifacts
              ? `Expand artifacts panel (${artifacts.length} items)`
              : 'Artifacts panel (empty)'
          }
        >
          {/* Top: vertical title so the user knows what this column is */}
          <span className="text-[11px] font-semibold [writing-mode:vertical-rl] tracking-wider whitespace-nowrap">
            {hasArtifacts ? `Artifacts (${artifacts.length})` : 'Artifacts'}
          </span>
          {/* Spacer to push the chevron to the bottom */}
          <span className="flex-1" />
          {/* Bottom: chevron pointing left to suggest "open out toward the chat" */}
          <ChevronLeft className="w-4 h-4 opacity-50 group-hover:opacity-100 transition-opacity" aria-hidden />
        </button>
        {/* Refresh — always available, even when the panel is empty. */}
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="opacity-50 hover:opacity-100 transition-opacity disabled:opacity-30"
          title="Refresh artifacts"
          aria-label="Refresh artifacts"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
        </button>
      </div>
    );
  }

  const minimized = artifacts.filter((a) => minimizedTabIds.has(a.artifact_id));

  const visibleArtifacts = artifacts.filter((a) => !minimizedTabIds.has(a.artifact_id));
  const effectiveActiveId =
    activeId && visibleArtifacts.some((a) => a.artifact_id === activeId)
      ? activeId
      : visibleArtifacts[0]?.artifact_id ?? null;
  const active = visibleArtifacts.find((a) => a.artifact_id === effectiveActiveId);
  const zoomed = zoomedId
    ? visibleArtifacts.find((a) => a.artifact_id === zoomedId) ?? null
    : null;

  // Expanded mode: respect parent's flex-grow override if provided; otherwise
  // keep the legacy 2-share proportion via the `flex-[2]` shorthand. Setting
  // flex-basis: 0 alongside an explicit flexGrow makes the column's actual
  // width track grow ratios cleanly (no flex-basis: auto surprises).
  const expandedStyle =
    flexGrow !== undefined ? { flexGrow, flexBasis: 0 } : undefined;
  const expandedClass =
    flexGrow !== undefined
      ? 'flex flex-col min-w-[320px] border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden'
      : 'flex flex-col min-w-[320px] flex-[2] border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden';

  return (
    <aside className={expandedClass} style={expandedStyle} data-help-id="layout.artifacts">
      {/* Minimized strip — only renders when something is minimized.
          Click a chip to restore the tab. */}
      {minimized.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border-default)] bg-[var(--bg-secondary)] text-xs overflow-x-auto">
          <span className="opacity-60 whitespace-nowrap">⊟ Minimized:</span>
          <div className="flex gap-1.5 flex-1 min-w-0">
            {minimized.map((a) => (
              <button
                key={a.artifact_id}
                onClick={() => restoreTab(a.artifact_id)}
                className="px-2 py-0.5 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--bg-tertiary)] truncate max-w-[14rem] text-left"
                title={`Restore "${a.title}"`}
              >
                ↺ {a.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Header row: tab strip on the left, action buttons on the right.
          Shares its bottom border with the tab strip's own border-b. */}
      <div className="flex items-center justify-between min-w-0">
        <div className="flex-1 min-w-0">
          <ArtifactTabStrip agentId={agentId} onZoom={setZoomedId} />
        </div>
        <div className="flex items-center gap-1 px-1 border-b border-[var(--border-default)] self-stretch">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="text-xs opacity-60 hover:opacity-100 px-2 flex items-center disabled:opacity-40"
            title="Refresh artifacts"
            aria-label="Refresh artifacts"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
          </button>
          {active && (
            <button
              onClick={() => setZoomedId(active.artifact_id)}
              className="text-xs opacity-60 hover:opacity-100 px-2 flex items-center"
              title="Zoom artifact (open fullscreen)"
              aria-label="Zoom artifact"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
          )}
          {active && <ArtifactDownloadMenu artifact={active} />}
          <button
            onClick={() => setCollapsed(true)}
            className="text-xs opacity-60 hover:opacity-100 px-2"
            title="Collapse panel"
            aria-label="Collapse artifacts panel"
          >
            ▶
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden relative">
        {/* Live LRU pool for echarts artifacts: every id in chartLruOrder
            stays mounted (display:none when not active) so clicking back to
            a recent chart is instant — no re-fetch, no re-init. Oldest id
            falls off → ChartRenderer unmounts → echarts dispose() runs. */}
        {chartLruOrder
          .map((id) => artifacts.find((a) => a.artifact_id === id))
          .filter((a): a is NonNullable<typeof a> => Boolean(a))
          .map((a) => (
            <div
              key={a.artifact_id}
              className="absolute inset-0"
              style={{ display: a.artifact_id === activeId ? 'block' : 'none' }}
            >
              <ArtifactRenderer artifact={a} />
            </div>
          ))}
        {active && active.kind !== 'application/vnd.echarts+json' ? (
          <ArtifactRenderer artifact={active} />
        ) : null}
        {!active && <div className="p-4 opacity-60">Select an artifact</div>}
      </div>

      {/* Fullscreen zoom modal — portal'd to body, dimmed + blurred backdrop.
          Keyed by artifact id so each open is a fresh mount (zoom resets). */}
      <ArtifactZoomModal
        key={zoomed?.artifact_id ?? 'closed'}
        artifact={zoomed}
        onClose={() => setZoomedId(null)}
      />
    </aside>
  );
}
