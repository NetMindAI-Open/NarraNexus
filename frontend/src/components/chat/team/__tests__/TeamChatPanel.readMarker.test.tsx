/**
 * @file_name: TeamChatPanel.readMarker.test.tsx
 * @description: Reading the room clears its mark, and keeps clearing it.
 *
 * The sidebar can only mark a room read up to what the LIST response told it —
 * one timestamp, refreshed when the sidebar refreshes. The panel sees the actual
 * transcript, three seconds at a time, so it is the surface that knows what the
 * user has really looked at.
 *
 * Pinned here rather than in `lib/unread` because the helpers are already unit
 * tested there and the way this fails is at the seam: a marker advanced once on
 * mount and never again leaves the row marked while the user sits reading the
 * very messages that marked it.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';

import { getTeamLastReadMs } from '@/lib/unread';

let chatMessages: Array<Record<string, unknown>> = [];

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: () =>
      Promise.resolve({ success: true, messages: chatMessages, activity: [], lead_agent_id: null }),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: () => Promise.resolve([]),
    listTeamFiles: () => Promise.resolve([]),
    listTeamArtifactTurns: () => Promise.resolve({}),
  },
}));

// Stable identities: a fresh `notePatrol` per selector call would change the
// room's `refresh` every render and re-arm its poll effect without end.
const PATROL_BY_TEAM: Record<string, boolean> = {};
const NOTE_PATROL = () => {};

vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) =>
    select({ teams: TEAMS, patrolByTeam: PATROL_BY_TEAM, notePatrol: NOTE_PATROL }),
  useConfigStore: (select: (s: unknown) => unknown) =>
    select({ agents: AGENTS, displayName: 'Bin', userId: 'usr_1' }),
  useChatStore: (select: (s: unknown) => unknown) => select({ workspaceRefreshTick: 0 }),
}));

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNavigate: () => () => {},
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { TeamChatPanel } from '../TeamChatPanel';

const AGENTS = [{ agent_id: 'a1', name: 'Ana' }];
const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
];

function msg(id: string, at: string, extra: Record<string, unknown> = {}) {
  return {
    message_id: id,
    from_agent: 'a1',
    author_name: 'Ana',
    is_user: false,
    content: id,
    created_at: at,
    ...extra,
  };
}

/** Comfortably past the panel's poll interval. Deliberately not imported from
 *  the panel: if the poll ever slows past this, the test should fail loudly
 *  rather than silently follow it. */
const POLL_WINDOW_MS = 10_000;

beforeEach(() => {
  localStorage.clear();
  chatMessages = [];
});

describe('the open room marks itself read', () => {
  test('reading a room advances the marker to its newest message', async () => {
    chatMessages = [msg('m1', '2026-08-13T09:00:00Z'), msg('m2', '2026-08-13T09:05:00Z')];

    render(<TeamChatPanel teamId="t1" />);

    await waitFor(() =>
      expect(getTeamLastReadMs('t1')).toBe(Date.parse('2026-08-13T09:05:00Z')),
    );
  });

  test('an empty room marks nothing', async () => {
    // Opening a room nobody has spoken in must not write a watermark — the
    // first reply would then have to beat "now" to be marked.
    const { container } = render(<TeamChatPanel teamId="t1" />);

    await waitFor(() => expect(container.firstChild).toBeTruthy());
    expect(getTeamLastReadMs('t1')).toBe(0);
  });

  test('the marker keeps up with messages arriving while the room is open', async () => {
    // The failure this exists for: marking once on mount leaves the row marked
    // while the user sits reading the very messages that marked it.
    //
    // Driven through the POLL rather than a rerender, because a rerender alone
    // fetches nothing — `refresh` is memoised on teamId precisely so the 3s
    // interval is not torn down and recreated on every message.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      chatMessages = [msg('m1', '2026-08-13T09:00:00Z')];
      render(<TeamChatPanel teamId="t1" />);
      await waitFor(() =>
        expect(getTeamLastReadMs('t1')).toBe(Date.parse('2026-08-13T09:00:00Z')),
      );

      // What the next poll returns: the newer message the incremental `since`
      // window would carry.
      chatMessages = [msg('m2', '2026-08-13T09:30:00Z')];
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_WINDOW_MS);
      });

      await waitFor(() =>
        expect(getTeamLastReadMs('t1')).toBe(Date.parse('2026-08-13T09:30:00Z')),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  test('a platform line the user has seen counts as read', async () => {
    // The opposite rule from the server's, deliberately: the server decides what
    // is WORTH a mark, the panel records what has been SEEN. A room whose newest
    // line is a roster notice would otherwise stay marked with nothing the user
    // could do to clear it.
    chatMessages = [msg('m1', '2026-08-13T09:40:00Z', { msg_type: 'system_roster' })];

    render(<TeamChatPanel teamId="t1" />);

    await waitFor(() =>
      expect(getTeamLastReadMs('t1')).toBe(Date.parse('2026-08-13T09:40:00Z')),
    );
  });
});
