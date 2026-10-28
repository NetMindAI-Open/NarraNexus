/**
 * @file_name: artifactStore.test.ts
 * @description: Behavior contract for the artifact tab store.
 *
 * Covers the state logic that had no tests before: upsert merge semantics
 * (new → prepend + auto-activate, existing → replace in place), remove with
 * active-tab fallback, the echarts LRU (promote on activate, cap at 5,
 * drop on remove), minimize/restore, and the per-agent cache used by the
 * stale-while-revalidate loaders.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';

const listSessionMock = vi.fn();
const listPinnedMock = vi.fn();
// loadPinned pulls scope=context since 2026-08-18 (own pinned ∪ team) — the
// mock keeps the old variable name because every existing test's intent
// ("what the panel refresh returns") is unchanged.
const listContextMock = listPinnedMock;
vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    listSession: (...args: unknown[]) => listSessionMock(...args),
    listPinned: (...args: unknown[]) => listPinnedMock(...args),
    listContext: (...args: unknown[]) => listContextMock(...args),
  },
}));

import { useArtifactStore } from '../artifactStore';
import type { Artifact } from '@/types/artifact';

function makeArtifact(id: string, overrides: Partial<Artifact> = {}): Artifact {
  return {
    artifact_id: id,
    agent_id: 'agent_x',
    user_id: 'user_y',
    session_id: null,
    original_session_id: null,
    title: `Artifact ${id}`,
    kind: 'text/html',
    description: null,
    pinned: true,
    file_path: `sub/${id}.html`,
    size_bytes: 10,
    created_at: '2026-07-21T00:00:00Z',
    updated_at: '2026-07-21T00:00:00Z',
    ...overrides,
  } as Artifact;
}

const CHART_KIND = 'application/vnd.echarts+json';

beforeEach(() => {
  listSessionMock.mockReset();
  listPinnedMock.mockReset();
  useArtifactStore.setState({
    artifactsByAgent: {},
    activeAgentId: 'agent_x',
    artifacts: [],
    activeArtifactId: null,
    minimizedTabIds: new Set<string>(),
    chartLruOrder: [],
    chartInstances: {},
  });
  window.localStorage.clear();
});

describe('upsert', () => {
  test('new artifact for the active agent prepends and becomes active', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));

    const s = useArtifactStore.getState();
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_2', 'art_1']);
    // The newest upsert steals focus — that is the "tab pops up live" behavior.
    expect(s.activeArtifactId).toBe('art_2');
  });

  test('existing artifact is replaced in place without stealing focus', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));
    useArtifactStore.getState().setActive('art_1');

    useArtifactStore.getState().upsert(makeArtifact('art_2', { title: 'updated' }));

    const s = useArtifactStore.getState();
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_2', 'art_1']);
    expect(s.artifacts[0].title).toBe('updated');
    expect(s.activeArtifactId).toBe('art_1');
  });

  test('artifact of a background agent lands in its cache, not the visible list', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_bg', { agent_id: 'agent_other' }));

    const s = useArtifactStore.getState();
    expect(s.artifacts).toEqual([]);
    expect(s.activeArtifactId).toBeNull();
    expect(s.artifactsByAgent['agent_other'].map((a) => a.artifact_id)).toEqual(['art_bg']);
  });

  // register_artifact success is an explicit focus signal: even when the
  // artifact is ALREADY in the list (a list refresh raced ahead of the
  // tool_output roundtrip, or the agent re-registered an existing doc),
  // the panel must switch to it — otherwise the user stays on the old
  // Welcome tab and reads the successful generation as a failure.
  test('focus option activates an artifact that already exists in the list', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_new'));
    useArtifactStore.getState().upsert(makeArtifact('art_welcome'));
    useArtifactStore.getState().setActive('art_welcome');

    useArtifactStore.getState().upsert(makeArtifact('art_new', { title: 'regenerated' }), { focus: true });

    expect(useArtifactStore.getState().activeArtifactId).toBe('art_new');
  });

  test('focus option un-minimizes the artifact so the tab is actually visible', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));
    useArtifactStore.getState().minimizeTab('art_1');

    useArtifactStore.getState().upsert(makeArtifact('art_1', { title: 'back' }), { focus: true });

    const s = useArtifactStore.getState();
    expect(s.activeArtifactId).toBe('art_1');
    expect(s.minimizedTabIds.has('art_1')).toBe(false);
  });

  test('focus option does not steal focus for a background agent', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));

    useArtifactStore
      .getState()
      .upsert(makeArtifact('art_bg', { agent_id: 'agent_other' }), { focus: true });

    expect(useArtifactStore.getState().activeArtifactId).toBe('art_1');
  });
});

describe('remove', () => {
  test('removing the active tab activates the next remaining one', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));
    expect(useArtifactStore.getState().activeArtifactId).toBe('art_2');

    useArtifactStore.getState().remove('art_2');

    const s = useArtifactStore.getState();
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_1']);
    expect(s.activeArtifactId).toBe('art_1');
  });

  test('removing a background tab keeps the current selection', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));

    useArtifactStore.getState().remove('art_1');

    expect(useArtifactStore.getState().activeArtifactId).toBe('art_2');
  });

  test('removing the last tab clears the selection', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().remove('art_1');
    expect(useArtifactStore.getState().activeArtifactId).toBeNull();
  });
});

describe('chart LRU', () => {
  test('activating charts promotes to head and caps the list at 5', () => {
    for (let i = 1; i <= 6; i++) {
      useArtifactStore.getState().upsert(makeArtifact(`chart_${i}`, { kind: CHART_KIND }));
    }
    // Upserts auto-activate, so chart_6..chart_2 already fill the LRU (cap 5).
    const afterUpserts = useArtifactStore.getState().chartLruOrder;
    expect(afterUpserts).toEqual(['chart_6', 'chart_5', 'chart_4', 'chart_3', 'chart_2']);
    expect(afterUpserts).not.toContain('chart_1');

    // Re-activating an existing member moves it to the head without growing.
    useArtifactStore.getState().setActive('chart_3');
    expect(useArtifactStore.getState().chartLruOrder).toEqual([
      'chart_3', 'chart_6', 'chart_5', 'chart_4', 'chart_2',
    ]);
  });

  test('non-chart kinds never enter the LRU', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_html'));
    useArtifactStore.getState().setActive('art_html');
    expect(useArtifactStore.getState().chartLruOrder).toEqual([]);
  });

  test('removing a chart drops it from the LRU', () => {
    useArtifactStore.getState().upsert(makeArtifact('chart_1', { kind: CHART_KIND }));
    useArtifactStore.getState().upsert(makeArtifact('chart_2', { kind: CHART_KIND }));
    expect(useArtifactStore.getState().chartLruOrder).toEqual(['chart_2', 'chart_1']);

    useArtifactStore.getState().remove('chart_2');

    // chart_2 is gone; chart_1 is re-promoted as the new active tab.
    expect(useArtifactStore.getState().chartLruOrder).toEqual(['chart_1']);
  });
});

describe('minimize / restore', () => {
  test('minimizing the active tab moves focus to the first visible tab', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().upsert(makeArtifact('art_2'));
    expect(useArtifactStore.getState().activeArtifactId).toBe('art_2');

    useArtifactStore.getState().minimizeTab('art_2');

    const s = useArtifactStore.getState();
    expect(s.minimizedTabIds.has('art_2')).toBe(true);
    // art_2 stays in the list (registry untouched) — only the strip hides it.
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_2', 'art_1']);
    expect(s.activeArtifactId).toBe('art_1');
  });

  test('restore un-hides and re-activates the tab', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().minimizeTab('art_1');
    useArtifactStore.getState().restoreTab('art_1');

    const s = useArtifactStore.getState();
    expect(s.minimizedTabIds.has('art_1')).toBe(false);
    expect(s.activeArtifactId).toBe('art_1');
  });

  test('minimized ids survive via localStorage', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_1'));
    useArtifactStore.getState().minimizeTab('art_1');
    const raw = window.localStorage.getItem('artifact_minimized_ids');
    expect(JSON.parse(raw ?? '[]')).toEqual(['art_1']);
  });
});

describe('loaders (stale-while-revalidate)', () => {
  test('loadForSession shows cache immediately, then commits pinned-first merge', async () => {
    const cached = [makeArtifact('art_cached')];
    useArtifactStore.setState({
      artifactsByAgent: { agent_x: cached },
      activeAgentId: null,
      artifacts: [],
    });
    const pinned = [makeArtifact('art_pin')];
    const session = [makeArtifact('art_sess', { pinned: false, session_id: 's1' })];
    let resolveFetch!: () => void;
    const gate = new Promise<void>((r) => (resolveFetch = r));
    listPinnedMock.mockReturnValue(gate.then(() => pinned));
    listSessionMock.mockReturnValue(gate.then(() => session));

    const load = useArtifactStore.getState().loadForSession('agent_x', 's1');
    // Cache is visible synchronously, before the fetch resolves.
    expect(useArtifactStore.getState().artifacts).toEqual(cached);
    expect(useArtifactStore.getState().activeArtifactId).toBe('art_cached');

    resolveFetch();
    await load;

    const s = useArtifactStore.getState();
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_pin', 'art_sess']);
    expect(s.artifactsByAgent['agent_x'].map((a) => a.artifact_id)).toEqual(['art_pin', 'art_sess']);
  });

  test('loadForSession discards the fetch result if the user already switched agents', async () => {
    listPinnedMock.mockResolvedValue([makeArtifact('art_stale')]);
    listSessionMock.mockResolvedValue([]);

    const load = useArtifactStore.getState().loadForSession('agent_x', 's1');
    // User switches away before the fetch lands.
    useArtifactStore.setState({ activeAgentId: 'agent_other', artifacts: [] });
    await load;

    expect(useArtifactStore.getState().artifacts).toEqual([]);
  });
});

describe('0802 regression pack — active/minimize/registry invariants', () => {

  test('remove of the active tab never lands active on a minimized tab, and prunes state', () => {
    const a = makeArtifact('a', { kind: CHART_KIND });
    const b = makeArtifact('b');
    const c = makeArtifact('c');
    useArtifactStore.setState({
      artifacts: [a, b, c],
      activeArtifactId: 'a',
      minimizedTabIds: new Set(['b']), // b hidden → must be skipped as fallback
      chartInstances: { a: [{ getDataURL: () => '' }] },
    });
    useArtifactStore.getState().remove('a');
    const st = useArtifactStore.getState();
    expect(st.activeArtifactId).toBe('c'); // not list[0] === b (minimized)
    expect(st.minimizedTabIds.has('a')).toBe(false);
    expect(st.chartInstances.a).toBeUndefined();
  });

  test('restoreTab promotes a chart into the LRU so an instance can mount', () => {
    const a = makeArtifact('a', { kind: CHART_KIND });
    useArtifactStore.setState({ artifacts: [a], minimizedTabIds: new Set(['a']), chartLruOrder: [] });
    useArtifactStore.getState().restoreTab('a');
    expect(useArtifactStore.getState().activeArtifactId).toBe('a');
    expect(useArtifactStore.getState().chartLruOrder).toContain('a');
  });

  test('minimizeTab of the active chart promotes the new active chart', () => {
    const a = makeArtifact('a', { kind: CHART_KIND });
    const b = makeArtifact('b', { kind: CHART_KIND });
    useArtifactStore.setState({ artifacts: [a, b], activeArtifactId: 'a', chartLruOrder: ['a'] });
    useArtifactStore.getState().minimizeTab('a');
    expect(useArtifactStore.getState().activeArtifactId).toBe('b');
    expect(useArtifactStore.getState().chartLruOrder).toContain('b');
  });

  test('loadPinned skips a minimized pinned[0] and promotes the first visible chart (the refresh-blank path)', async () => {
    const c1 = makeArtifact('c1', { kind: CHART_KIND });
    const c2 = makeArtifact('c2', { kind: CHART_KIND });
    listPinnedMock.mockResolvedValue([c1, c2]);
    // c1 is pinned[0] but minimized (persisted across refresh in real usage).
    useArtifactStore.setState({
      activeAgentId: 'agent_x',
      minimizedTabIds: new Set(['c1']),
      chartLruOrder: [],
    });
    await useArtifactStore.getState().loadPinned('agent_x');
    const st = useArtifactStore.getState();
    // Active must be the first VISIBLE tab, and the LRU head must match it,
    // or ArtifactColumn's pool renders every pane display:none → blank column.
    expect(st.activeArtifactId).toBe('c2');
    expect(st.chartLruOrder[0]).toBe('c2');
  });
});

describe('chart instance registry is a per-id list (0802 ②)', () => {
  test('co-mounted instances coexist; unmount removes only its own; download uses the latest', () => {
    const columnInstance = { getDataURL: () => 'column' };
    const modalInstance = { getDataURL: () => 'modal' };
    const store = useArtifactStore.getState();
    store.registerChartInstance('a', columnInstance); // column pane mounts
    store.registerChartInstance('a', modalInstance); // zoom modal mounts on top
    expect(useArtifactStore.getState().chartInstances.a).toEqual([columnInstance, modalInstance]);

    // Modal closes: only its entry leaves; the column instance stays live so
    // the download menu (which reads the last entry) still works.
    store.unregisterChartInstance('a', modalInstance);
    expect(useArtifactStore.getState().chartInstances.a).toEqual([columnInstance]);

    // Column unmounts: list empties → key dropped.
    store.unregisterChartInstance('a', columnInstance);
    expect(useArtifactStore.getState().chartInstances.a).toBeUndefined();
  });

  test('unregister of an already-gone instance is a no-op', () => {
    const inst = { getDataURL: () => '' };
    useArtifactStore.getState().unregisterChartInstance('a', inst);
    expect(useArtifactStore.getState().chartInstances.a).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// applyEvent (2026-08-18, spec artifact-events §3.3): the store's entry point
// for backend-pushed artifact_changed events. Replaces the chat-stream
// string-matching discovery path entirely.
// ---------------------------------------------------------------------------

function makeEvent(
  action: 'registered' | 'updated' | 'deleted' | 'repointed',
  artifact: Artifact,
  extra?: Record<string, unknown>,
) {
  return { type: 'artifact_changed', action, external: false, artifact, ...(extra ? { extra } : {}) };
}

describe('applyEvent', () => {
  test('registered upserts and focuses for the active agent', () => {
    useArtifactStore.getState().applyEvent(makeEvent('registered', makeArtifact('art_ev1')));

    const s = useArtifactStore.getState();
    expect(s.artifacts.map((a) => a.artifact_id)).toEqual(['art_ev1']);
    expect(s.activeArtifactId).toBe('art_ev1');
  });

  test('updated with an OLDER updated_at than the store copy is ignored', () => {
    useArtifactStore.getState().upsert(
      makeArtifact('art_ev2', { title: 'fresh', updated_at: '2026-08-18T10:00:00Z' }),
    );
    useArtifactStore.getState().applyEvent(
      makeEvent('updated', makeArtifact('art_ev2', { title: 'stale', updated_at: '2026-08-18T09:00:00Z' })),
    );

    const row = useArtifactStore.getState().artifacts.find((a) => a.artifact_id === 'art_ev2');
    expect(row?.title).toBe('fresh'); // monotonic guard: late events never regress state
  });

  test('updated with a newer updated_at replaces the store copy', () => {
    useArtifactStore.getState().upsert(
      makeArtifact('art_ev3', { title: 'old', updated_at: '2026-08-18T09:00:00Z' }),
    );
    useArtifactStore.getState().applyEvent(
      makeEvent('updated', makeArtifact('art_ev3', { title: 'new', updated_at: '2026-08-18T10:00:00Z' })),
    );

    const row = useArtifactStore.getState().artifacts.find((a) => a.artifact_id === 'art_ev3');
    expect(row?.title).toBe('new');
  });

  test('deleted removes the artifact and clears its minimized entry', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_ev4'));
    useArtifactStore.getState().minimizeTab('art_ev4');

    useArtifactStore.getState().applyEvent(makeEvent('deleted', makeArtifact('art_ev4')));

    const s = useArtifactStore.getState();
    expect(s.artifacts.find((a) => a.artifact_id === 'art_ev4')).toBeUndefined();
    expect(s.minimizedTabIds.has('art_ev4')).toBe(false);
  });

  test('repointed upserts without stealing focus from another tab', () => {
    useArtifactStore.getState().upsert(makeArtifact('art_ev5', { updated_at: '2026-08-18T09:00:00Z' }));
    useArtifactStore.getState().upsert(makeArtifact('art_ev6'));
    expect(useArtifactStore.getState().activeArtifactId).toBe('art_ev6');

    useArtifactStore.getState().applyEvent(
      makeEvent(
        'repointed',
        makeArtifact('art_ev5', { updated_at: '2026-08-18T10:00:00Z' }),
        { old: 'report/a.html', new: 'report/b.html', hash_matched: true },
      ),
    );

    const s = useArtifactStore.getState();
    // Content refresh rides updated_at; the user's current tab stays put.
    expect(s.activeArtifactId).toBe('art_ev6');
    expect(s.artifacts.find((a) => a.artifact_id === 'art_ev5')?.updated_at).toBe('2026-08-18T10:00:00Z');
  });

  test('malformed events are swallowed loudly, never thrown', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() =>
      useArtifactStore.getState().applyEvent({ type: 'artifact_changed' } as never),
    ).not.toThrow();
    spy.mockRestore();
  });
});
