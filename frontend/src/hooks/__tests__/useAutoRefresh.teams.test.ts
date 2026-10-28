/**
 * @file_name: useAutoRefresh.teams.test.ts
 * @description: The sidebar's team rows are kept fresh in the background.
 *
 * The room-activity mark on a team row is a comparison between a client
 * watermark and a server timestamp that arrives with the TEAM LIST. Nothing
 * refreshed that list on a timer: it was fetched once, when the sidebar found it
 * unloaded. So a room could talk for an hour and the mark would appear only on
 * the next full page reload — a feature indistinguishable, from the user's side,
 * from one that does not work.
 *
 * The second thing pinned here is the guard. The scheduler used to require a
 * selected AGENT before starting at all, which meant a user sitting in a team
 * room could have no background refresh whatsoever. Every poll that needs an
 * agent still checks for one itself.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

// Async, like the store's own `refresh(): Promise<void>` — a sync mock would
// make the hook's `.then` throw and pin a contract the store does not have.
const teamsRefresh = vi.fn(async () => {});
const refreshAgents = vi.fn();

vi.mock('@/stores', () => ({
  // `teams` is part of the store's shape, so the mock carries it — the woken-room
  // check reads it right after every refresh.
  useTeamsStore: { getState: () => ({ refresh: teamsRefresh, teams: [] }) },
  useConfigStore: { getState: () => ({ refreshAgents, agents: [] }) },
  useChatStore: { getState: () => ({ isAgentStreaming: () => false }) },
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

/** Past the 30s mid tick with room to spare — if that interval ever grows past
 *  this, the test should fail rather than quietly follow it. */
const MID_WINDOW_MS = 45_000;

beforeEach(() => {
  teamsRefresh.mockReset();
  refreshAgents.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('background refresh keeps the team rows current', () => {
  test('the mid tick refreshes teams', () => {
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).toHaveBeenCalled();
  });

  test('teams and the agent list refresh even with no agent selected', () => {
    // A user whose window is on a team room — or a brand-new user whose guide
    // agent is being provisioned server-side. Teams AND the agent list sit
    // ahead of the agent guard (2026-08-19: before that, a zero-agent user's
    // sidebar never refreshed and the auto-provisioned guide agent only
    // appeared on a manual reload); the agent-scoped polls below still guard
    // on an agent id of their own.
    renderHook(() => useAutoRefresh({ agentId: '', userId: 'usr_1' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).toHaveBeenCalled();
    expect(refreshAgents).toHaveBeenCalled();
  });

  test('the first-login fast poll fetches agents while the guide coachmark is armed', () => {
    // A brand-new user: the server is provisioning their guide agent in the
    // background, the coachmark is armed, and the sidebar is empty. The 30s
    // tick is too slow for a first impression — a 2s bounded poll covers it.
    window.localStorage.setItem('nx-guide-coachmark', 'pending');
    try {
      renderHook(() => useAutoRefresh({ agentId: '', userId: 'usr_new' }));

      vi.advanceTimersByTime(2_000);
      expect(refreshAgents).toHaveBeenCalledTimes(1);

      // Bounded: /api/auth/agents is enriched and must not be fast-polled
      // forever — the poll gives up after ~20s.
      vi.advanceTimersByTime(60_000);
      const callsAfterCap = refreshAgents.mock.calls.length;
      vi.advanceTimersByTime(10_000);
      expect(refreshAgents.mock.calls.length).toBe(callsAfterCap);
    } finally {
      window.localStorage.removeItem('nx-guide-coachmark');
    }
  });

  test('the fast-poll cap is wall-clock — a hidden tab exhausts it silently', () => {
    // attempts count BEFORE the hidden skip on purpose: a backgrounded tab
    // makes zero requests AND burns the ~20s budget, so the interval is gone
    // by the time the tab returns (the 30s tick covers late returns). Moving
    // the count after the hidden check turns the cap into "foreground time"
    // and leaves the interval armed for the whole session.
    window.localStorage.setItem('nx-guide-coachmark', 'pending');
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
      renderHook(() => useAutoRefresh({ agentId: '', userId: 'usr_new' }));

      vi.advanceTimersByTime(22_000); // 11 ticks, all hidden → budget spent
      expect(refreshAgents).not.toHaveBeenCalled();

      hidden.mockReturnValue(false);
      vi.advanceTimersByTime(6_000); // still under the 30s mid tick
      expect(refreshAgents).not.toHaveBeenCalled(); // interval already cleared
    } finally {
      hidden.mockRestore();
      window.localStorage.removeItem('nx-guide-coachmark');
    }
  });

  test('no fast poll without the coachmark armed', () => {
    renderHook(() => useAutoRefresh({ agentId: '', userId: 'usr_1' }));

    vi.advanceTimersByTime(4_000); // under the 30s mid tick
    expect(refreshAgents).not.toHaveBeenCalled();
  });

  test('nothing polls without a user', () => {
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: '' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).not.toHaveBeenCalled();
  });

  test('a hidden tab polls nothing', () => {
    // The whole point of the visibility guard: zero requests while the tab is in
    // the background. A team refresh added past it would reopen that.
    const spy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
      renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
      vi.advanceTimersByTime(MID_WINDOW_MS);
      expect(teamsRefresh).not.toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });

  test('returning to the tab refreshes teams immediately', () => {
    // Coming back is exactly when the user wants to know what happened while
    // they were gone; waiting up to 30s for the next tick shows a stale sidebar
    // at the one moment it is being read.
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
    teamsRefresh.mockReset();

    document.dispatchEvent(new Event('visibilitychange'));

    expect(teamsRefresh).toHaveBeenCalled();
  });
});
