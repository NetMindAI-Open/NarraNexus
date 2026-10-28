/**
 * Artifact store — manages open artifact tabs, active selection, and collapse state.
 *
 * Real-time artifact signals arrive via the existing chat WebSocket stream (tool_output
 * frames parsed in ChatPanel.tsx). This store does NOT manage a dedicated WS connection.
 */

import { create } from 'zustand';
import type { Artifact } from '@/types/artifact';
import { removeDraft } from '@/lib/artifactEditing/drafts';
import { artifactsApi } from '@/services/artifactsApi';

const MINIMIZED_IDS_KEY = 'artifact_minimized_ids';

/** Wire shape of a backend artifact_changed event (see backend notify.py). */
export interface ArtifactChangedEvent {
  type: 'artifact_changed';
  action: 'registered' | 'updated' | 'deleted' | 'repointed';
  /** True when the change was detected rather than committed (external edit). */
  external?: boolean;
  artifact: Artifact;
  /** repointed only: heal's old/new path tails + whether the hash verified. */
  extra?: { old?: string; new?: string; hash_matched?: boolean };
}

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

  /**
   * Live registry of mounted chart renderers, keyed by artifact_id. The
   * value is a LIST because one artifact can be mounted more than once at
   * a time (the column pane and the zoom modal both mount it); a single
   * slot let the modal's unmount clear the column's still-live instance,
   * so downloads reported "not ready". Consumers use the last mounted
   * instance. ChartRenderer appends on mount, removes by identity on
   * unmount; the key is dropped when the list empties.
   */
  chartInstances: Record<string, ChartInstanceLike[]>;

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

  /**
   * Artifact ids whose editing surface holds unsaved text. Written by the
   * editor hook's host component, read by ArtifactTabStrip (dirty dot).
   * Content safety does NOT depend on this — the localStorage draft layer in
   * useArtifactEditor carries the text — this is only the visual layer of
   * the dirty guard.
   */
  editorDirtyIds: Set<string>;

  loadForSession: (agentId: string, sessionId: string) => Promise<void>;
  loadPinned: (agentId: string) => Promise<void>;
  /**
   * Apply one backend-pushed artifact_changed event (spec artifact-events
   * §3.3). This is the store's ONLY realtime input — it replaced the chat
   * stream string-matching path. Late/duplicate events are neutralised by
   * an updated_at monotonic guard, so callers never need to order or dedupe.
   * Never throws: a malformed event logs and is dropped (the full pull on
   * open/switch/reconnect is the self-healing floor).
   */
  applyEvent: (event: ArtifactChangedEvent) => void;
  setActive: (artifactId: string | null) => void;
  /**
   * Insert or replace an artifact. New artifacts of the active agent
   * auto-focus. `focus: true` additionally forces focus (and un-minimizes)
   * even when the artifact already exists — used by the register_artifact
   * success signal so a generated/regenerated doc always comes to front
   * instead of the panel staying on the previous tab.
   */
  upsert: (artifact: Artifact, opts?: { focus?: boolean }) => void;
  remove: (artifactId: string) => void;
  setEditorDirty: (artifactId: string, dirty: boolean) => void;
  registerChartInstance: (artifactId: string, instance: ChartInstanceLike) => void;
  /** Identity-checked clear: only the mount that registered may remove. */
  unregisterChartInstance: (artifactId: string, instance: ChartInstanceLike) => void;
  minimizeTab: (artifactId: string) => void;
  restoreTab: (artifactId: string) => void;

  pin: (agentId: string, artifactId: string, pinned: boolean) => Promise<void>;
  delete: (agentId: string, artifactId: string) => Promise<void>;
  /** Open a web page as a URL-tab artifact and focus it. */
  openUrl: (agentId: string, url: string, title?: string) => Promise<Artifact>;
}

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
 * The single source of truth for "which tab is active" whenever the list or
 * the minimized set changes: keep `preferred` only if it is present AND
 * visible, else the first visible tab, else null. Every active-id write
 * point funnels through here so `activeArtifactId` can never name a hidden
 * tab — a hidden active pointer blanks the whole column (0802 ①⑤), and it
 * used to reappear via each loader/remove path independently. NOT used by
 * upsert's focus branch, which deliberately un-minimizes then focuses.
 */
function _pickVisibleActive(
  list: Artifact[],
  minimized: Set<string>,
  preferred: string | null,
): string | null {
  if (preferred && !minimized.has(preferred) && list.some((a) => a.artifact_id === preferred)) {
    return preferred;
  }
  return list.find((a) => !minimized.has(a.artifact_id))?.artifact_id ?? null;
}

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
  chartInstances: {},
  minimizedTabIds: initialMinimizedTabIds,
  chartLruOrder: [],
  editorDirtyIds: new Set<string>(),

  setEditorDirty(artifactId, dirty) {
    set((state) => {
      if (state.editorDirtyIds.has(artifactId) === dirty) return state;
      const next = new Set(state.editorDirtyIds);
      if (dirty) next.add(artifactId);
      else next.delete(artifactId);
      return { editorDirtyIds: next };
    });
  },

  async loadForSession(agentId, sessionId) {
    // Stale-while-revalidate: switch the active agent and surface any cached
    // artifacts immediately, then fetch in the background. Avoids a blank
    // panel on every agent switch.
    const cached = get().artifactsByAgent[agentId] ?? [];
    const nextActiveCached = _pickVisibleActive(
      cached,
      get().minimizedTabIds,
      get().activeArtifactId,
    );
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
    const nextActiveMerged = _pickVisibleActive(
      merged,
      get().minimizedTabIds,
      get().activeArtifactId,
    );
    set((state) => ({
      artifactsByAgent: { ...get().artifactsByAgent, [agentId]: merged },
      artifacts: merged,
      activeArtifactId: nextActiveMerged,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActiveMerged, merged),
    }));
  },

  async loadPinned(agentId) {
    // Stale-while-revalidate: switch + show cached immediately, then refresh.
    // Since 2026-08-18 the refresh pulls scope=context (own pinned ∪ team
    // artifacts — the agent's full awareness surface) so the panel and the
    // agent's state block can never disagree about what exists. The method
    // keeps its historical name because "load this agent's panel" is still
    // exactly what it does.
    const cached = get().artifactsByAgent[agentId] ?? [];
    const nextActiveCached = _pickVisibleActive(
      cached,
      get().minimizedTabIds,
      get().activeArtifactId,
    );
    set((state) => ({
      activeAgentId: agentId,
      artifacts: cached,
      activeArtifactId: nextActiveCached,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActiveCached, cached),
    }));

    const pinned = await artifactsApi.listContext(agentId);
    if (get().activeAgentId !== agentId) return;
    const nextActivePinned = _pickVisibleActive(
      pinned,
      get().minimizedTabIds,
      get().activeArtifactId,
    );
    set((state) => ({
      artifactsByAgent: { ...get().artifactsByAgent, [agentId]: pinned },
      artifacts: pinned,
      activeArtifactId: nextActivePinned,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, nextActivePinned, pinned),
    }));
  },

  applyEvent(event) {
    try {
      const artifact = event?.artifact;
      if (!artifact?.artifact_id) return;
      if (event.action === 'deleted') {
        get().remove(artifact.artifact_id);
        return;
      }
      // Monotonic guard: an event older than (or equal to) what the store
      // already holds is a replay or an out-of-order delivery — never let it
      // regress state. Equal timestamps are skipped too: same-commit replays
      // are common on reconnect (event_stream replay + full pull racing).
      const known =
        (get().artifactsByAgent[artifact.agent_id] ?? []).find(
          (a) => a.artifact_id === artifact.artifact_id,
        ) ?? get().artifacts.find((a) => a.artifact_id === artifact.artifact_id);
      if (known && Date.parse(known.updated_at) >= Date.parse(artifact.updated_at)) {
        return;
      }
      // registered (unknown row) auto-focuses via upsert's new-artifact rule;
      // updated/repointed refresh in place without stealing the user's tab —
      // content reload rides updated_at (useArtifactRawUrl's refreshKey).
      get().upsert(artifact);
    } catch (e) {
      // Loud, never fatal: the WS dispatcher must survive any one bad event.
      console.error('artifact event apply failed', e, event);
    }
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

  upsert(artifact, opts) {
    const list = get().artifacts;
    const idx = list.findIndex((a) => a.artifact_id === artifact.artifact_id);
    const nextList = idx === -1 ? [artifact, ...list] : list.map((a, i) => (i === idx ? artifact : a));
    const agentId = artifact.agent_id;
    const isActiveAgent = get().activeAgentId === agentId;
    const takeFocus = isActiveAgent && (idx === -1 || opts?.focus === true);
    const newActiveId = takeFocus ? artifact.artifact_id : get().activeArtifactId;
    if (takeFocus && get().minimizedTabIds.has(artifact.artifact_id)) {
      // A focused tab must actually be visible — TabStrip filters minimized ids.
      const next = new Set(get().minimizedTabIds);
      next.delete(artifact.artifact_id);
      persistMinimizedTabIds(next);
      set({ minimizedTabIds: next });
    }
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
    // A removed artifact's editor draft must not squat in localStorage
    // forever (quota is shared; a full quota silently kills EVERY draft —
    // review #334 I8). deleted-events funnel through remove(), so this one
    // hook covers both the local delete and the pushed deletion. Key
    // template lives in ONE place (drafts.ts — r2 I5).
    removeDraft(artifactId);
    const list = get().artifacts.filter((a) => a.artifact_id !== artifactId);
    const cache = { ...get().artifactsByAgent };
    for (const aid of Object.keys(cache)) {
      cache[aid] = cache[aid].filter((a) => a.artifact_id !== artifactId);
    }
    // Next active must be a VISIBLE tab (0802 ⑤): every write point funnels
    // through _pickVisibleActive so a hidden tab can never become active.
    const minimized = new Set(get().minimizedTabIds);
    const wasMinimized = minimized.delete(artifactId);
    if (wasMinimized) persistMinimizedTabIds(minimized);
    const newActiveId =
      get().activeArtifactId === artifactId
        ? _pickVisibleActive(list, minimized, null)
        : get().activeArtifactId;
    set((state) => {
      const instances = { ...state.chartInstances };
      delete instances[artifactId];
      return {
        artifacts: list,
        artifactsByAgent: cache,
        activeArtifactId: newActiveId,
        minimizedTabIds: minimized,
        chartInstances: instances,
        // Drop the removed id from the LRU and re-promote the new active so a
        // dispose-on-delete unmounts the canvas immediately.
        chartLruOrder: _promoteChartLru(
          state.chartLruOrder.filter((id) => id !== artifactId),
          newActiveId,
          list,
        ),
      };
    });
  },

  registerChartInstance(artifactId, instance) {
    // Append: one artifact can be mounted twice (column pane + zoom modal).
    set((state) => ({
      chartInstances: {
        ...state.chartInstances,
        [artifactId]: [...(state.chartInstances[artifactId] ?? []), instance],
      },
    }));
  },

  unregisterChartInstance(artifactId, instance) {
    // Remove BY IDENTITY only (0802 ②): the dying mount drops its own entry
    // and leaves any co-mounted instance registered, so a zoom-modal close
    // no longer strands the column's still-live chart with an empty slot
    // (which made downloads report "not ready"). Drop the key when empty.
    set((state) => {
      const rest = (state.chartInstances[artifactId] ?? []).filter((c) => c !== instance);
      const next = { ...state.chartInstances };
      if (rest.length) next[artifactId] = rest;
      else delete next[artifactId];
      return { chartInstances: next };
    });
  },

  minimizeTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    next.add(artifactId);
    persistMinimizedTabIds(next);
    const currentActive = get().activeArtifactId;
    const newActive =
      currentActive === artifactId
        ? _pickVisibleActive(get().artifacts, next, null)
        : currentActive;
    set((state) => ({
      minimizedTabIds: next,
      activeArtifactId: newActive,
      // Promote so a chart landing active actually has a mounted instance
      // (0802 ①: minimize/restore were the only paths skipping the LRU).
      chartLruOrder: _promoteChartLru(state.chartLruOrder, newActive, state.artifacts),
    }));
  },

  restoreTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    // The inline badge / preview card / omnibox route a plain "open this
    // artifact" click here, so guard the localStorage write on an actual
    // change — most clicks don't touch the minimized set.
    if (next.delete(artifactId)) persistMinimizedTabIds(next);
    set((state) => ({
      minimizedTabIds: next,
      activeArtifactId: artifactId,
      chartLruOrder: _promoteChartLru(state.chartLruOrder, artifactId, state.artifacts),
    }));
  },

  async pin(agentId, artifactId, pinned) {
    const updated = await artifactsApi.setPinned(agentId, artifactId, pinned);
    get().upsert(updated);
  },

  async delete(agentId, artifactId) {
    await artifactsApi.remove(agentId, artifactId);
    get().remove(artifactId);
  },

  async openUrl(agentId, url, title) {
    const artifact = await artifactsApi.openUrl(agentId, url, title);
    get().upsert(artifact); // upsert auto-focuses a new tab
    return artifact;
  },
}));
