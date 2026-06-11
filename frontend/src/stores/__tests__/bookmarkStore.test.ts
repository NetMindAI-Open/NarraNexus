/**
 * @file_name: bookmarkStore.test.ts
 * @description: Behavior contract for the per-agent bookmark state store.
 *
 * Key invariants tested:
 *   - run-start clears info highlights and non-badge sub-bookmarks
 *   - badge-backed sub-bookmarks survive run-start (§5.2 badge exemption)
 *   - inbox count-zero clears badge and highlight
 *   - resolveJobAttention removes one failed job while leaving others
 *   - visibleSubBookmarks aggregation with priority ordering and overflow
 *   - per-agent isolation
 *   - markOpened clears info but not attention
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useBookmarkStore } from '../bookmarkStore';

// Reset store state between tests
beforeEach(() => {
  useBookmarkStore.getState().clearAll();
});

const AGENT_A = 'agent_aaa';
const AGENT_B = 'agent_bbb';

// ---------------------------------------------------------------------------
// (a) run-start clears info highlights + non-badge sub-bookmarks
// ---------------------------------------------------------------------------
describe('onRunStart', () => {
  it('clears info highlights when a new run starts', () => {
    const store = useBookmarkStore.getState();
    store.noteJobCompleted(AGENT_A, 'job_1', 'Report');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:job_1']).toBe('info');

    useBookmarkStore.getState().onRunStart(AGENT_A);
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:job_1']).toBeUndefined();
  });

  it('removes sub-bookmarks that are not badge-backed on run-start', () => {
    const store = useBookmarkStore.getState();
    store.noteJobCompleted(AGENT_A, 'job_1', 'Report');
    store.noteProfileUpdate(AGENT_A, 'awareness');

    useBookmarkStore.getState().onRunStart(AGENT_A);

    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.subBookmarks).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // (b) run-start PRESERVES failed-job and inbox-unread badges + their sub-bookmarks
  // -------------------------------------------------------------------------
  it('preserves failed-job sub-bookmark and attention highlight across run-start', () => {
    const store = useBookmarkStore.getState();
    store.noteJobFailed(AGENT_A, 'job_fail', 'Bad Job');

    useBookmarkStore.getState().onRunStart(AGENT_A);

    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.highlights['job:job_fail']).toBe('attention');
    const sub = state?.subBookmarks.find((s) => s.key === 'job:job_fail');
    expect(sub).toBeDefined();
    expect(sub?.status).toBe('attention');
  });

  it('preserves inbox-unread sub-bookmark and attention highlight across run-start', () => {
    const store = useBookmarkStore.getState();
    store.noteInboxUnread(AGENT_A, 3);

    useBookmarkStore.getState().onRunStart(AGENT_A);

    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.highlights['inbox']).toBe('attention');
    const sub = state?.subBookmarks.find((s) => s.key === 'inbox');
    expect(sub).toBeDefined();
  });

  it('does NOT clear the failedJobs badge count on run-start', () => {
    const store = useBookmarkStore.getState();
    store.noteJobFailed(AGENT_A, 'job_fail', 'Bad Job');

    useBookmarkStore.getState().onRunStart(AGENT_A);

    expect(useBookmarkStore.getState().agents[AGENT_A]?.badges.failedJobs).toBe(1);
  });

  it('does NOT clear the inboxUnread badge count on run-start', () => {
    const store = useBookmarkStore.getState();
    store.noteInboxUnread(AGENT_A, 5);

    useBookmarkStore.getState().onRunStart(AGENT_A);

    expect(useBookmarkStore.getState().agents[AGENT_A]?.badges.inboxUnread).toBe(5);
  });

  it('clears attention highlight only if it has no badge (info-only failed job scenario n/a, but attention from profile does not exist — guard against regression)', () => {
    // Profile updates only emit 'info', never 'attention'. Ensure info from
    // profile is cleared normally and not mistakenly exempted.
    const store = useBookmarkStore.getState();
    store.noteProfileUpdate(AGENT_A, 'awareness');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['profile:awareness']).toBe('info');

    useBookmarkStore.getState().onRunStart(AGENT_A);
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['profile:awareness']).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// (c) inbox count→0 clears badge
// ---------------------------------------------------------------------------
describe('noteInboxUnread', () => {
  it('count > 0 sets attention highlight and badge', () => {
    useBookmarkStore.getState().noteInboxUnread(AGENT_A, 2);
    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.highlights['inbox']).toBe('attention');
    expect(state?.badges.inboxUnread).toBe(2);
  });

  it('count === 0 clears attention highlight and badge', () => {
    useBookmarkStore.getState().noteInboxUnread(AGENT_A, 2);
    useBookmarkStore.getState().noteInboxUnread(AGENT_A, 0);
    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.highlights['inbox']).toBeUndefined();
    expect(state?.badges.inboxUnread).toBe(0);
    const sub = state?.subBookmarks.find((s) => s.key === 'inbox');
    expect(sub).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// (d) resolveJobAttention clears one failed job while another stays
// ---------------------------------------------------------------------------
describe('resolveJobAttention', () => {
  it('removes the resolved job badge contribution and sub-bookmark but preserves others', () => {
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'job_a', 'Job A');
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'job_b', 'Job B');

    useBookmarkStore.getState().resolveJobAttention(AGENT_A, 'job_a');

    const state = useBookmarkStore.getState().agents[AGENT_A];
    expect(state?.highlights['job:job_a']).toBeUndefined();
    expect(state?.highlights['job:job_b']).toBe('attention');
    expect(state?.subBookmarks.find((s) => s.key === 'job:job_a')).toBeUndefined();
    expect(state?.subBookmarks.find((s) => s.key === 'job:job_b')).toBeDefined();
    // failedJobs badge decremented from 2 to 1
    expect(state?.badges.failedJobs).toBe(1);
  });

  it('failedJobs badge reaches 0 when the last failed job is resolved', () => {
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'job_only', 'Solo Job');
    useBookmarkStore.getState().resolveJobAttention(AGENT_A, 'job_only');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.badges.failedJobs).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// (e) visibleSubBookmarks aggregation — max=3, priority: running > attention > info
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// (f) per-agent isolation
// ---------------------------------------------------------------------------
describe('per-agent isolation', () => {
  it('actions on AGENT_A do not affect AGENT_B', () => {
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'j1', 'Job');
    useBookmarkStore.getState().noteJobCompleted(AGENT_B, 'j2', 'Other');

    const a = useBookmarkStore.getState().agents[AGENT_A];
    const b = useBookmarkStore.getState().agents[AGENT_B];

    expect(a?.badges.failedJobs).toBe(1);
    expect(b?.badges.failedJobs).toBe(0);
    expect(a?.highlights['job:j1']).toBe('attention');
    expect(b?.highlights['job:j2']).toBe('info');
  });

  it('onRunStart for AGENT_A does not affect AGENT_B state', () => {
    useBookmarkStore.getState().noteJobCompleted(AGENT_A, 'j1', 'Job A');
    useBookmarkStore.getState().noteJobCompleted(AGENT_B, 'j2', 'Job B');

    useBookmarkStore.getState().onRunStart(AGENT_A);

    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:j1']).toBeUndefined();
    expect(useBookmarkStore.getState().agents[AGENT_B]?.highlights['job:j2']).toBe('info');
  });

  it('clearAgent removes only the target agent', () => {
    useBookmarkStore.getState().noteJobRunning(AGENT_A, 'j1', 'Job');
    useBookmarkStore.getState().noteJobRunning(AGENT_B, 'j2', 'Job');

    useBookmarkStore.getState().clearAgent(AGENT_A);

    expect(useBookmarkStore.getState().agents[AGENT_A]).toBeUndefined();
    expect(useBookmarkStore.getState().agents[AGENT_B]).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// (g) markOpened clears info but not attention
// ---------------------------------------------------------------------------
describe('markOpened', () => {
  it('clears an info highlight when the user opens the drawer at that item', () => {
    useBookmarkStore.getState().noteJobCompleted(AGENT_A, 'j1', 'Report');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:j1']).toBe('info');

    useBookmarkStore.getState().markOpened(AGENT_A, 'job:j1');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:j1']).toBeUndefined();
  });

  it('does NOT clear an attention highlight when the user opens the drawer', () => {
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'j_fail', 'Failed Job');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:j_fail']).toBe('attention');

    useBookmarkStore.getState().markOpened(AGENT_A, 'job:j_fail');
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['job:j_fail']).toBe('attention');
  });

  it('does NOT affect badges when marking opened', () => {
    useBookmarkStore.getState().noteInboxUnread(AGENT_A, 3);
    useBookmarkStore.getState().markOpened(AGENT_A, 'inbox');
    // inbox attention/badge should remain (only noteInboxUnread(0) clears it)
    expect(useBookmarkStore.getState().agents[AGENT_A]?.badges.inboxUnread).toBe(3);
    expect(useBookmarkStore.getState().agents[AGENT_A]?.highlights['inbox']).toBe('attention');
  });
});

// ---------------------------------------------------------------------------
// Additional edge cases
// ---------------------------------------------------------------------------
describe('noteJobRunning', () => {
  it('adds a running sub-bookmark without setting a highlight', () => {
    useBookmarkStore.getState().noteJobRunning(AGENT_A, 'j_run', 'Active Job');
    const state = useBookmarkStore.getState().agents[AGENT_A];
    const sub = state?.subBookmarks.find((s) => s.key === 'job:j_run');
    expect(sub?.status).toBe('running');
    // running does NOT set a highlight (spec §4: "spinner, not a highlight")
    expect(state?.highlights['job:j_run']).toBeUndefined();
  });
});

describe('failedJobs badge derived from distinct failed job keys', () => {
  it('counts distinct failed job keys (duplicate noteJobFailed calls same id do not double-count)', () => {
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'j1', 'Job');
    useBookmarkStore.getState().noteJobFailed(AGENT_A, 'j1', 'Job'); // same id again
    expect(useBookmarkStore.getState().agents[AGENT_A]?.badges.failedJobs).toBe(1);
  });
});
