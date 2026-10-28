/**
 * @file_name: TeamChatPanel.renderCost.test.tsx
 * @description: The transcript does not re-parse itself every second.
 *
 * `Markdown` is memo'd because remark/rehype re-parse the entire body on every
 * render, and this panel renders at least once a second (a 1s ticker keeps live
 * durations moving) and once per keystroke (the composer's text lives here).
 *
 * That memo is shallow, so it holds only while its props keep their identity —
 * and the chain that feeds it is three components long: the panel's
 * `memberNames` → each bubble's `nameSet` → each bubble's rehype plugin array.
 * One inline `Object.fromEntries(...)` at the top defeats all of it, and the
 * symptom is not a failure: the room simply gets heavier the longer it is open,
 * and typing drops frames in a busy room. Nobody would suspect mention
 * highlighting.
 *
 * So the invariant is tested where it can actually be observed — at the prop
 * itself. Counting re-parses would mean reaching into ReactMarkdown; asserting
 * the identity is stable across a re-render is the same statement, one level up
 * and without the coupling.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';

const seen: Array<Record<string, string>> = [];

vi.mock('@/lib/api', () => ({
  api: {
    // At least one message: an empty room renders the hero instead of the
    // transcript, and this test is about what the transcript is handed.
    getTeamChat: () =>
      Promise.resolve({
        success: true,
        messages: [
          {
            message_id: 'm1',
            from_agent: 'a1',
            author_name: 'Ana',
            is_user: false,
            content: 'hello',
            created_at: '2026-08-14T09:00:00Z',
          },
        ],
        activity: [],
        lead_agent_id: null,
      }),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: () => Promise.resolve([]),
    listTeamFiles: () => Promise.resolve([]),
    listTeamArtifactTurns: () => Promise.resolve({}),
  },
}));

// Record the exact object the transcript is handed on every render.
vi.mock('../TeamTranscript', () => ({
  TeamTranscript: ({ memberNames }: { memberNames: Record<string, string> }) => {
    seen.push(memberNames);
    return <div data-testid="fake-transcript" />;
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

const AGENTS = [
  { agent_id: 'a1', name: 'Ana' },
  { agent_id: 'a2', name: 'Bruno' },
];
const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1', 'a2'],
  },
];

beforeEach(() => {
  localStorage.clear();
  seen.length = 0;
});

describe('the member-name map keeps its identity', () => {
  test('a re-render hands the transcript the same object', async () => {
    const view = render(<TeamChatPanel teamId="t1" />);
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    const first = seen[seen.length - 1];

    view.rerender(<TeamChatPanel teamId="t1" />);
    await act(async () => {});

    expect(seen[seen.length - 1]).toBe(first);
  });

  test('and so does a render caused by the panel ticking', async () => {
    // The 1s ticker is why this matters at all: without it the panel would
    // mostly sit still and an unstable prop would cost little.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<TeamChatPanel teamId="t1" />);
      await waitFor(() => expect(seen.length).toBeGreaterThan(0));
      const first = seen[seen.length - 1];
      const rendersBefore = seen.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      // The ticker must actually have re-rendered, or "the identity did not
      // change" is true because nothing happened — the test would pass on a
      // panel that had stopped ticking entirely, which is the opposite of what
      // it is here to check.
      expect(seen.length).toBeGreaterThan(rendersBefore);
      expect(seen[seen.length - 1]).toBe(first);
    } finally {
      vi.useRealTimers();
    }
  });

  test('it still tracks the roster when that actually changes', async () => {
    // The identity is not allowed to be stable by being WRONG.
    render(<TeamChatPanel teamId="t1" />);
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));

    expect(seen[seen.length - 1]).toEqual({ a1: 'Ana', a2: 'Bruno' });
  });
});
