/**
 * @file_name: useAutoRefresh.teamToast.test.ts
 * @description: Noticing that a room started talking, without becoming noise.
 *
 * The sidebar dot answers "has anything happened" only while the user is
 * looking at the sidebar. A room that wakes up while they are reading something
 * else is the case the dot cannot cover — and it is the common one, because the
 * room is async precisely so they can be elsewhere.
 *
 * Everything here is about the second half of that sentence: WITHOUT BECOMING
 * NOISE. A toast per new message in a room where six agents answer at once is a
 * notification the user turns off, and then the feature is worse than absent.
 * So the trigger is the EDGE — a room the user had caught up on has started
 * talking — not the level. A room that is already unread stays unread until they
 * open it, and says nothing more in the meantime.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { markTeamRead } from '@/lib/unread';

let teams: Array<Record<string, unknown>> = [];
const setToasts = vi.fn();

const teamsRefresh = vi.fn(async () => {});

vi.mock('@/stores', () => ({
  useTeamsStore: { getState: () => ({ refresh: teamsRefresh, teams }) },
  useConfigStore: { getState: () => ({ refreshAgents: vi.fn(), agents: [] }) },
  useChatStore: {
    getState: () => ({ isAgentStreaming: () => false }),
    setState: (fn: unknown) => setToasts(fn),
  },
  useArtifactStore: (select: (s: unknown) => unknown) => select({ loadPinned: vi.fn() }),
  usePreloadStore: () => ({
    refreshAgentInbox: vi.fn(),
    refreshJobs: vi.fn(),
    refreshAwareness: vi.fn(),
    refreshChatHistory: vi.fn(),
    refreshSocialNetwork: vi.fn(),
  }),
}));

vi.mock('@/lib/api', () => ({
  api: { getSimpleChatHistory: () => Promise.resolve({ success: true, messages: [] }) },
}));

import { useAutoRefresh } from '../useAutoRefresh';

const MID_WINDOW_MS = 45_000;

function team(id: string, at: string | null, name = 'Desk') {
  return {
    team: { team_id: id, name, owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
    last_message_at: at,
  };
}

/** The toasts a `useChatStore.setState` updater would have produced. */
function queuedToasts() {
  const out: Array<Record<string, unknown>> = [];
  for (const call of setToasts.mock.calls) {
    const updater = call[0] as (s: unknown) => { toastQueue?: unknown[] };
    const next = updater({ toastQueue: [], completedAgentIds: [] });
    for (const item of next.toastQueue ?? []) out.push(item as Record<string, unknown>);
  }
  return out;
}

/** Two mid ticks: the first observes, the second can notice a change. */
async function tickTwice() {
  await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);
  await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);
}

beforeEach(() => {
  localStorage.clear();
  setToasts.mockReset();
  teamsRefresh.mockClear();
  teams = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('a room that starts talking says so once', () => {
  test('a room going from caught-up to talking raises a toast', async () => {
    markTeamRead('t1', Date.parse('2026-08-13T09:00:00Z'));
    teams = [team('t1', '2026-08-13T09:00:00Z')];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);
    teams = [team('t1', '2026-08-13T09:30:00Z')];
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    const toasts = queuedToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatchObject({ kind: 'team', teamId: 't1', teamName: 'Desk' });
  });

  test('a room that keeps talking does not toast again', async () => {
    // The level would fire every 30s for as long as the room is busy. The edge
    // fires once: the room stays unread until the user opens it, and repeating
    // "still unread" is what teaches people to dismiss notifications unread.
    markTeamRead('t1', Date.parse('2026-08-13T09:00:00Z'));
    teams = [team('t1', '2026-08-13T09:00:00Z')];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);
    teams = [team('t1', '2026-08-13T09:30:00Z')];
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);
    teams = [team('t1', '2026-08-13T09:45:00Z')];
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    expect(queuedToasts()).toHaveLength(1);
  });

  test('the first observation never toasts', async () => {
    // Otherwise every unread room the user has ever left announces itself on
    // app start — at the one moment they did not ask about any of them.
    teams = [team('t1', '2026-08-13T09:30:00Z')];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    expect(queuedToasts()).toHaveLength(0);
  });

  test('a room the user is reading does not toast', async () => {
    // The open room advances its own watermark every 3s (TeamChatPanel), so by
    // the time the 30s tick sees the new message it is already read. No route
    // knowledge needed here: "already seen" is the honest test, and it is the
    // same one the sidebar dot uses.
    teams = [team('t1', '2026-08-13T09:00:00Z')];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    teams = [team('t1', '2026-08-13T09:30:00Z')];
    markTeamRead('t1', Date.parse('2026-08-13T09:30:00Z'));
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    expect(queuedToasts()).toHaveLength(0);
  });

  test('a room that has never spoken does not toast', async () => {
    teams = [team('t1', null)];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    await tickTwice();

    expect(queuedToasts()).toHaveLength(0);
  });

  test('a newly added team is observed before it can toast', async () => {
    // A team created (or joined) between two ticks has no prior observation.
    // Treating "absent last time" as "was caught up" would toast for the whole
    // backlog of a room the user just gained access to.
    teams = [];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    teams = [team('t2', '2026-08-13T09:30:00Z')];
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    expect(queuedToasts()).toHaveLength(0);
  });

  test('each room is tracked separately', async () => {
    markTeamRead('t1', Date.parse('2026-08-13T09:00:00Z'));
    markTeamRead('t2', Date.parse('2026-08-13T09:00:00Z'));
    teams = [team('t1', '2026-08-13T09:00:00Z', 'Desk'), team('t2', '2026-08-13T09:00:00Z', 'Lab')];
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    teams = [team('t1', '2026-08-13T09:00:00Z', 'Desk'), team('t2', '2026-08-13T09:30:00Z', 'Lab')];
    await vi.advanceTimersByTimeAsync(MID_WINDOW_MS);

    const toasts = queuedToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatchObject({ teamId: 't2', teamName: 'Lab' });
  });
});
