/**
 * Artifact store — manages open artifact tabs, active selection, and collapse state.
 *
 * Real-time artifact signals arrive via the existing chat WebSocket stream (tool_output
 * frames parsed in ChatPanel.tsx). This store does NOT manage a dedicated WS connection.
 */

import { create } from 'zustand';
import type { Artifact } from '@/types/artifact';
import { artifactsApi } from '@/services/artifactsApi';

const COLLAPSED_KEY = 'artifact_column_collapsed';
const MINIMIZED_IDS_KEY = 'artifact_minimized_ids';

/**
 * Minimal interface to the echarts instance methods we actually use.
 * Avoids a hard type dependency on echarts in this eagerly-loaded store
 * (echarts itself is lazy-loaded inside ChartRenderer).
 */
export interface ChartInstanceLike {
  getDataURL: (opts: {
    type?: 'png' | 'jpeg' | 'svg';
    backgroundColor?: string;
    pixelRatio?: number;
  }) => string;
}

interface ArtifactState {
  /**
   * Per-agent cache so switching agents back-and-forth shows the previous
   * artifacts immediately while a stale-while-revalidate refresh runs in
   * the background. Keys are agent_id, values are the latest known artifact
   * list for that agent.
   */
  artifactsByAgent: Record<string, Artifact[]>;
  /** Currently displayed agent — drives the `artifacts` view. */
  activeAgentId: string | null;
  /** Convenience selector: artifacts for the active agent. */
  artifacts: Artifact[];
  activeArtifactId: string | null;
  collapsed: boolean;

  /**
   * Live registry of mounted chart renderers, keyed by artifact_id.
   * Used by ArtifactDownloadMenu to call getDataURL() for PNG/JPEG export.
   * ChartRenderer registers on mount, unregisters on unmount.
   */
  chartInstances: Record<string, ChartInstanceLike | null>;

  /**
   * Tab IDs the user has clicked "minimize" on. The artifact stays in `artifacts`
   * (and in the DB), but TabStrip filters them out and surfaces them in the
   * "Minimized" header bar so the user can restore them. Persisted to
   * localStorage so refreshes do not undo the user's intent.
   */
  minimizedTabIds: Set<string>;

  /**
   * LRU of recently-active echarts artifact_ids — newest first, length ≤
   * CHART_LRU_LIMIT. ArtifactColumn keeps each id in this list mounted
   * (display: hidden when not active) so flipping back to a recent chart is
   * instant — no re-fetch, no re-init. When an id falls off the tail the
   * ChartRenderer unmounts, `chart.dispose()` runs, and the canvas / option
   * tree are released. setActive() promotes a chart to the head on every
   * click. HTML / CSV / Markdown / PDF / image artifacts are unaffected.
   */
  chartLruOrder: string[];

  loadForSession: (agentId: string, sessionId: string) => Promise<void>;
  loadPinned: (agentId: string) => Promise<void>;
  setActive: (artifactId: string | null) => void;
  upsert: (artifact: Artifact) => void;
  remove: (artifactId: string) => void;
  setCollapsed: (collapsed: boolean) => void;
  registerChartInstance: (artifactId: string, instance: ChartInstanceLike | null) => void;
  minimizeTab: (artifactId: string) => void;
  restoreTab: (artifactId: string) => void;

  pin: (agentId: string, artifactId: string, pinned: boolean) => Promise<void>;
  delete: (agentId: string, artifactId: string) => Promise<void>;
}

const initialCollapsed = (() => {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
})();

const initialMinimizedTabIds = (() => {
  try {
    const raw = window.localStorage.getItem(MINIMIZED_IDS_KEY);
    if (!raw) return new Set<string>();
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return new Set<string>();
    return new Set<string>(arr.filter((x): x is string => typeof x === 'string'));
  } catch {
    return new Set<string>();
  }
})();

function persistMinimizedTabIds(ids: Set<string>): void {
  try {
    window.localStorage.setItem(MINIMIZED_IDS_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    /* ignore */
  }
}

const CHART_LRU_LIMIT = 5;
const ECHARTS_KIND = 'application/vnd.echarts+json';

/**
 * Move `artifactId` to the head of the chart LRU if (and only if) it points at
 * an echarts artifact in `artifacts`. Returns the input list unchanged for
 * non-chart kinds and missing rows, so the caller can pipe every active-id
 * change through here without branching.
 */
function _promoteChartLru(
  current: string[],
  artifactId: string | null,
  artifacts: Artifact[],
): string[] {
  if (!artifactId) return current;
  const art = artifacts.find((a) => a.artifact_id === artifactId);
  if (art?.kind !== ECHARTS_KIND) return current;
  const without = current.filter((id) => id !== artifactId);
  return [artifactId, ...without].slice(0, CHART_LRU_LIMIT);
}

export const useArtifactStore = create<ArtifactState>((set, get) => ({
  artifactsByAgent: {},
  activeAgentId: null,
  artifacts: [],
  activeArtifactId: null,
  collapsed: initialCollapsed,
  chartInstances: {},
  minimizedTabIds: initialMinimizedTabIds,
  chartLruOrder: [],

  async loadForSession(agentId, sessionId) {
    // Stale-while-revalidate: switch the active agent and surface any cached
    // artifacts immediately, then fetch in the background. Avoids a blank
    // panel on every agent switch.
    const cached = get().artifactsByAgent[agentId] ?? [];
    const nextActiveCached =
      cached.find((a) => a.artifact_id === get().activeArtifactId)
        ? get().activeArtifactId
        : cached[0]?.artifact_id ?? null;
    set((state) => ({
      activeAgentId: agentId,
      artifacts: cached,
      activeArtifactId: nextActiveCached,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActiveCached, cached),
    }));

    const [sessionArtifacts, pinned] = await Promise.all([
      artifactsApi.listSession(agentId, sessionId),
      artifactsApi.listPinned(agentId),
    ]);
    const merged = [
      ...pinned,
      ...sessionArtifacts.filter((a) => !pinned.find((p) => p.artifact_id === a.artifact_id)),
    ];
    // Only commit if the user is still on this agent.
    if (get().activeAgentId !== agentId) return;
    const nextActiveMerged =
      merged.find((a) => a.artifact_id === get().activeArtifactId)
        ? get().activeArtifactId
        : merged[0]?.artifact_id ?? null;
    set((state) => ({
      artifactsByAgent: { ...get().artifactsByAgent, [agentId]: merged },
      artifacts: merged,
      activeArtifactId: nextActiveMerged,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActiveMerged, merged),
    }));
  },

  async loadPinned(agentId) {
    // Stale-while-revalidate: switch + show cached immediately, then refresh.
    const cached = get().artifactsByAgent[agentId] ?? [];
    const nextActiveCached =
      cached.find((a) => a.artifact_id === get().activeArtifactId)
        ? get().activeArtifactId
        : cached[0]?.artifact_id ?? null;
    set((state) => ({
      activeAgentId: agentId,
      artifacts: cached,
      activeArtifactId: nextActiveCached,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActiveCached, cached),
    }));

    const pinned = await artifactsApi.listPinned(agentId);
    if (get().activeAgentId !== agentId) return;
    const nextActivePinned =
      pinned.find((a) => a.artifact_id === get().activeArtifactId)
        ? get().activeArtifactId
        : pinned[0]?.artifact_id ?? null;
    set((state) => ({
      artifactsByAgent: { ...get().artifactsByAgent, [agentId]: pinned },
      artifacts: pinned,
      activeArtifactId: nextActivePinned,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActivePinned, pinned),
    }));
  },

  setActive(artifactId) {
    set((state) => ({
      activeArtifactId: artifactId,
      // Every click on an echarts tab promotes that artifact to the head of
      // the LRU; the oldest in the tail falls off and ChartRenderer disposes
      // it on unmount. Non-chart kinds slide through unchanged.
      chartLruOrder: _promoteChartLru(state.chartLruOrder, artifactId, state.artifacts),
    }));
  },

  upsert(artifact) {
    const list = get().artifacts;
    const idx = list.findIndex((a) => a.artifact_id === artifact.artifact_id);
    const nextList = idx === -1 ? [artifact, ...list] : list.map((a, i) => (i === idx ? artifact : a));
    const agentId = artifact.agent_id;
    const isActiveAgent = get().activeAgentId === agentId;
    const newActiveId = idx === -1 && isActiveAgent ? artifact.artifact_id : get().activeArtifactId;
    set((state) => ({
      artifacts: isActiveAgent ? nextList : get().artifacts,
      artifactsByAgent: {
        ...get().artifactsByAgent,
        [agentId]:
          (() => {
            const cache = get().artifactsByAgent[agentId] ?? [];
            const ci = cache.findIndex((a) => a.artifact_id === artifact.artifact_id);
            return ci === -1 ? [artifact, ...cache] : cache.map((a, i) => (i === ci ? artifact : a));
          })(),
      },
      activeArtifactId: newActiveId,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, newActiveId, nextList),
    }));
  },

  remove(artifactId) {
    const list = get().artifacts.filter((a) => a.artifact_id !== artifactId);
    const cache = { ...get().artifactsByAgent };
    for (const aid of Object.keys(cache)) {
      cache[aid] = cache[aid].filter((a) => a.artifact_id !== artifactId);
    }
    const newActiveId =
      get().activeArtifactId === artifactId ? list[0]?.artifact_id ?? null : get().activeArtifactId;
    set((state) => ({
      artifacts: list,
      artifactsByAgent: cache,
      activeArtifactId: newActiveId,
      // Drop the removed id from the LRU and re-promote the new active so a
      // dispose-on-delete unmounts the canvas immediately.
      chartLruOrder: _promoteChartLru(
        state.chartLruOrder.filter((id) => id !== artifactId),
        newActiveId,
        list,
      ),
    }));
  },

  setCollapsed(collapsed) {
    set({ collapsed });
    try {
      window.localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore */
    }
  },

  registerChartInstance(artifactId, instance) {
    set((state) => ({
      chartInstances: { ...state.chartInstances, [artifactId]: instance },
    }));
  },

  minimizeTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    next.add(artifactId);
    persistMinimizedTabIds(next);
    // If this was the active tab, switch active to the first non-minimized one.
    const visible = get().artifacts.filter((a) => !next.has(a.artifact_id));
    const currentActive = get().activeArtifactId;
    set({
      minimizedTabIds: next,
      activeArtifactId:
        currentActive === artifactId ? visible[0]?.artifact_id ?? null : currentActive,
    });
  },

  restoreTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    next.delete(artifactId);
    persistMinimizedTabIds(next);
    set({ minimizedTabIds: next, activeArtifactId: artifactId });
  },

  async pin(agentId, artifactId, pinned) {
    const updated = await artifactsApi.setPinned(agentId, artifactId, pinned);
    get().upsert(updated);
  },

  async delete(agentId, artifactId) {
    await artifactsApi.remove(agentId, artifactId);
    get().remove(artifactId);
  },
}));
