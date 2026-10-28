/**
 * @file_name: TeamChatPanel.history.test.tsx
 * @description: Reaching the part of the conversation that scrolled off.
 *
 * The room fetched `limit=200` with no cursor, and the bus's `get_messages` is
 * `ORDER BY created_at ASC LIMIT n` — the OLDEST 200. A room that had said more
 * than that opened on its first day, and since every later poll walked FORWARD
 * from what was on screen, it stayed there. Nothing looked broken; the room
 * just showed the wrong end of itself, permanently.
 *
 * So this is two things at once: the room now opens on the newest page (server
 * side, pinned in tests/backend/test_team_chat_paging.py), and the older pages
 * need a way back. Reaching the top asks for the page above.
 *
 * The awkward part is scroll position. Prepending content moves everything the
 * reader is looking at down by exactly the height of what was added — so the
 * naive version teleports them away from the message they were reading, which
 * is the one thing a "load more" must not do.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const getTeamChat = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: (...a: unknown[]) => getTeamChat(...a),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: () => Promise.resolve([]),
    listTeamFiles: () => Promise.resolve([]),
    listTeamArtifactTurns: () => Promise.resolve({}),
    sendTeamChat: () => Promise.resolve({ success: true }),
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
  {
    team: { team_id: 't2', name: 'Lab', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
];

function msg(id: string, at: string) {
  return {
    message_id: id,
    from_agent: 'a1',
    author_name: 'Ana',
    is_user: false,
    content: id,
    created_at: at,
  };
}

const NEWEST = [msg('n1', '2026-08-14T10:00:00Z'), msg('n2', '2026-08-14T10:01:00Z')];
const OLDER = [msg('o1', '2026-08-14T09:00:00Z'), msg('o2', '2026-08-14T09:01:00Z')];

/** The page each call should get, by cursor. */
function serve(pages: { newest: unknown[]; older?: unknown[] }) {
  getTeamChat.mockImplementation((_team: string, since?: string, before?: string) => {
    if (before) return Promise.resolve({ success: true, messages: pages.older ?? [] });
    if (since) return Promise.resolve({ success: true, messages: [] });
    return Promise.resolve({ success: true, messages: pages.newest });
  });
}

function scroller(): HTMLElement {
  return screen.getByTestId('team-transcript-scroll');
}

/** jsdom gives every element zero height; a scroll container has to be faked. */
function sizeScroller(el: HTMLElement, scrollHeight: number, scrollTop: number) {
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true });
  Object.defineProperty(el, 'clientHeight', { value: 500, configurable: true });
  el.scrollTop = scrollTop;
}

beforeEach(() => {
  localStorage.clear();
  getTeamChat.mockReset();
  serve({ newest: NEWEST, older: OLDER });
});

describe('paging back through a room', () => {
  test('the first load asks for no cursor at all', async () => {
    // Which is what makes it the NEWEST page rather than the oldest.
    render(<TeamChatPanel teamId="t1" />);

    await waitFor(() => expect(getTeamChat).toHaveBeenCalled());
    expect(getTeamChat.mock.calls[0][1]).toBeUndefined();
    expect(getTeamChat.mock.calls[0][2]).toBeUndefined();
  });

  test('reaching the top asks for the page above', async () => {
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);

    await waitFor(() =>
      expect(
        getTeamChat.mock.calls.some((c) => c[2] === '2026-08-14T10:00:00Z'),
      ).toBe(true),
    );
  });

  test('the older page appears above what was on screen', async () => {
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);

    await screen.findByText('o1');
    const rendered = screen.getAllByText(/^[on]\d$/).map((n) => n.textContent);
    expect(rendered).toEqual(['o1', 'o2', 'n1', 'n2']);
  });

  test('scrolling near the top twice does not fire two overlapping loads', async () => {
    // Scroll events arrive in bursts. Without a guard the same page is fetched
    // several times and each response re-triggers the merge.
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);
    fireEvent.scroll(el);
    fireEvent.scroll(el);

    await screen.findByText('o1');
    const backwards = getTeamChat.mock.calls.filter((c) => c[2]);
    expect(backwards).toHaveLength(1);
  });

  test('an empty page stops it asking again', async () => {
    // The top of the history. Without this the room re-asks on every scroll
    // event for the rest of the session.
    serve({ newest: NEWEST, older: [] });
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);
    await act(async () => {});
    fireEvent.scroll(el);
    await act(async () => {});

    expect(getTeamChat.mock.calls.filter((c) => c[2])).toHaveLength(1);
  });

  test('an empty room never pages back', async () => {
    // With nothing on screen there is no page above; asking would refetch the
    // newest page under a cursor and merge it into itself.
    serve({ newest: [], older: OLDER });
    render(<TeamChatPanel teamId="t1" />);
    await waitFor(() => expect(getTeamChat).toHaveBeenCalled());

    const before = getTeamChat.mock.calls.length;
    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);
    await act(async () => {});

    // No request AT ALL, not merely no `before` request: without a cursor the
    // call degrades into a second fetch of the newest page, which then merges
    // into itself. Counting only cursored calls would miss that entirely.
    expect(getTeamChat.mock.calls.length).toBe(before);
  });

  test('the reader stays on the message they were reading', async () => {
    // Prepending moves everything down by the height of what was added. Leaving
    // scrollTop alone teleports the reader away from the message that made them
    // scroll up in the first place.
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 1000, 0);
    fireEvent.scroll(el);
    // The older page lands and the content grows by 800px.
    Object.defineProperty(el, 'scrollHeight', { value: 1800, configurable: true });

    await screen.findByText('o1');
    await waitFor(() => expect(el.scrollTop).toBe(800));
  });

  test('scrolling in the middle pages nothing', async () => {
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 900);
    fireEvent.scroll(el);
    await act(async () => {});

    expect(getTeamChat.mock.calls.filter((c) => c[2])).toHaveLength(0);
  });

  test('the wait is visible while the older page is in flight', async () => {
    // Scrolling to the top and seeing nothing happen looks exactly like having
    // reached the beginning of the room. The request takes as long as it takes;
    // what the reader needs is to know one is running.
    let release: (v: unknown) => void = () => {};
    getTeamChat.mockImplementation((_t: string, since?: string, before?: string) => {
      if (before) return new Promise((res) => { release = res; });
      if (since) return Promise.resolve({ success: true, messages: [] });
      return Promise.resolve({ success: true, messages: NEWEST });
    });
    render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);

    await screen.findByTestId('loading-older');
    await act(async () => {
      release({ success: true, messages: OLDER });
    });
    await waitFor(() => expect(screen.queryByTestId('loading-older')).toBeNull());
  });

  test('switching rooms fetches the new room fresh, not relative to the old one', async () => {
    // Found by the test below failing for the wrong reason. `refresh` reads the
    // transcript through a ref, and on a room switch that ref still held the
    // PREVIOUS room's messages — so the new room was fetched with `since` set
    // to a timestamp from a different conversation. Everything in the new room
    // older than that never arrived, and if the old room's last message was the
    // newer of the two, the new room rendered empty.
    const view = render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');

    view.rerender(<TeamChatPanel teamId="t2" />);

    await waitFor(() => expect(getTeamChat.mock.calls.some((c) => c[0] === 't2')).toBe(true));
    for (const call of getTeamChat.mock.calls.filter((c) => c[0] === 't2')) {
      expect(call[1]).toBeFalsy();
    }
  });

  test('switching rooms forgets that the top was reached', async () => {
    // Otherwise the second room inherits the first room's "no more history" and
    // silently refuses to page back at all.
    serve({ newest: NEWEST, older: [] });
    const view = render(<TeamChatPanel teamId="t1" />);
    await screen.findByText('n1');
    const el = scroller();
    sizeScroller(el, 2000, 0);
    fireEvent.scroll(el);
    await act(async () => {});

    serve({ newest: NEWEST, older: OLDER });
    view.rerender(<TeamChatPanel teamId="t2" />);
    // The new room's first page has to land before there is anything to page
    // ABOVE — without this the scroll below finds an empty transcript and
    // correctly does nothing, which would pass for the wrong reason.
    await waitFor(() =>
      expect(getTeamChat.mock.calls.some((c) => c[0] === 't2' && !c[1] && !c[2])).toBe(true),
    );
    await act(async () => {});
    const el2 = scroller();
    sizeScroller(el2, 2000, 0);
    fireEvent.scroll(el2);

    await waitFor(() =>
      expect(getTeamChat.mock.calls.filter((c) => c[0] === 't2' && c[2])).toHaveLength(1),
    );
  });
});
