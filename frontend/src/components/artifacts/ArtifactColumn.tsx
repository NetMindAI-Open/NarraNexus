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

import { useLayoutEffect, useRef, useState, type Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useArtifactStore } from '@/stores';
import ArtifactTabStrip from './ArtifactTabStrip';
import ArtifactDownloadMenu from './ArtifactDownloadMenu';
import ArtifactRenderer from './ArtifactRenderer';
import ArtifactZoomModal from './ArtifactZoomModal';

interface Props {
  agentId: string;
  /**
   * Optional flex-grow override. The parent layout passes this to drive the
   * chat ↔ artifacts split via the ResizableDivider. When omitted, falls back
   * to the legacy `flex-[2]` proportion.
   */
  flexGrow?: number;
  /**
   * Handle on the `<aside>`, so the parent's ResizableDivider can write
   * `flex-grow` straight to the DOM during a drag (live-follow with zero
   * React renders).
   */
  columnRef?: Ref<HTMLElement>;
  /**
   * True while the user is dragging the divider next to this column. The
   * content area then keeps the pixel width it had when the drag started
   * (the `<aside>` shrinks around it and clips), so a sandboxed HTML
   * artifact's `<iframe>` is NOT reflowed 60×/s. Cleared on release →
   * exactly one reflow, at the final width.
   */
  contentFrozen?: boolean;
}

export default function ArtifactColumn({
  agentId,
  flexGrow,
  columnRef,
  contentFrozen = false,
}: Props) {
  const { t } = useTranslation();
  // All hooks must run in the same order on every render — no conditional hook
  // calls. Selectors first, then early returns.
  const artifacts = useArtifactStore((s) => s.artifacts);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
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

  // Drag freeze (see `contentFrozen`). Measured in a LAYOUT effect so the
  // width is read before the browser paints the first dragged frame —
  // a passive `useEffect` could sample a width the drag had already changed.
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [frozenContentPx, setFrozenContentPx] = useState<number | null>(null);
  useLayoutEffect(() => {
    setFrozenContentPx(contentFrozen ? (contentRef.current?.offsetWidth ?? null) : null);
  }, [contentFrozen]);

  // The collapsed "sliver" form is gone: since v4 the only live mount is the
  // drawer (BookmarkPanelHost), which owns opening/closing, so the sliver
  // branch and the store's `collapsed` state were unreachable and removed.

  // Nothing yet — a calm empty state instead of a blank pane, since the
  // drawer's Artifacts tab is always present.
  if (artifacts.length === 0) {
    return (
      <aside
        className="chat-frosted flex flex-1 flex-col items-center justify-center min-w-0 p-6 text-center overflow-hidden"
        data-help-id="layout.artifacts"
      >
        <div className="text-sm text-[var(--text-secondary)]">{t('artifacts.emptyState.title')}</div>
        <div className="mt-1 text-xs text-[var(--text-tertiary)]">
          {t('artifacts.emptyState.hint')}
        </div>
      </aside>
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
      ? 'chat-frosted flex flex-col min-w-[320px] overflow-hidden'
      : 'chat-frosted flex flex-col min-w-[320px] flex-[2] overflow-hidden';

  return (
    <aside
      ref={columnRef}
      className={expandedClass}
      style={expandedStyle}
      data-help-id="layout.artifacts"
    >
      {/* Minimized strip — only renders when something is minimized.
          Click a chip to restore the tab. */}
      {minimized.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border-default)] bg-[var(--bg-secondary)] text-xs overflow-x-auto">
          <span className="opacity-60 whitespace-nowrap">⊟ {t('artifacts.minimized')}</span>
          <div className="flex gap-1.5 flex-1 min-w-0">
            {minimized.map((a) => (
              <button
                key={a.artifact_id}
                onClick={() => restoreTab(a.artifact_id)}
                className="px-2 py-0.5 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--nm-paper-warm)] truncate max-w-[14rem] text-left"
                title={t('artifacts.restore', { title: a.title })}
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
            title={t('artifacts.refresh')}
            aria-label={t('artifacts.refresh')}
          >
            <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
          </button>
          {active && (
            <button
              onClick={() => setZoomedId(active.artifact_id)}
              className="text-xs opacity-60 hover:opacity-100 px-2 flex items-center"
              title={t('artifacts.zoomFullscreen')}
              aria-label={t('artifacts.zoom')}
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
          )}
          {active && <ArtifactDownloadMenu artifact={active} />}
          {/* No collapse affordance: the drawer (BookmarkPanelHost) owns
              closing. The old collapse button and the store state behind it
              were removed with the sliver branch. */}
        </div>
      </div>
      <div
        ref={contentRef}
        className="flex-1 min-h-0 overflow-hidden relative"
        // `max(100%, frozen)` — a floor, not a fixed width:
        //   shrinking → holds at the pre-drag width and the <aside> clips it,
        //     so the artifact <iframe> is never reflowed while narrowing (the
        //     expensive, visibly janky direction);
        //   widening  → tracks the column, because a fixed width would leave a
        //     blank strip down the right of the pane for the whole drag, and
        //     the user reads that gap as the panel having broken.
        // Explicit width also overrides the column's `align-items: stretch`.
        // Vertical flex behaviour (flex-1 / min-h-0) is deliberately untouched.
        style={
          frozenContentPx !== null
            ? { width: `max(100%, ${frozenContentPx}px)` }
            : undefined
        }
      >
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
              style={{
                // effectiveActiveId, NOT raw activeId: when the raw pointer
                // names a hidden/minimized row the modal and `active` fall
                // back to a visible chart while this pool kept comparing the
                // raw id — every pane display:none = blank column (0802 ①).
                display: a.artifact_id === effectiveActiveId ? 'block' : 'none',
              }}
            >
              <ArtifactRenderer artifact={a} />
            </div>
          ))}
        {active && active.kind !== 'application/vnd.echarts+json' ? (
          <ArtifactRenderer artifact={active} />
        ) : null}
        {!active && <div className="p-4 opacity-60">{t('artifacts.selectArtifact')}</div>}
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
