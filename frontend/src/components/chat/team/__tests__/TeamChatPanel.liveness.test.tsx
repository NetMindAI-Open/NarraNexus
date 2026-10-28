/**
 * @file_name: TeamChatPanel.liveness.test.tsx
 * @description: The transcript shows a sign of life, not only a running turn.
 *
 * PRD "Team chat responsiveness" is about the room going visibly dead
 * after someone speaks. The backend has known better all along: the team GET
 * reports four states, and `queued` is computed straight from pending messages,
 * so it is true within one 3s poll of the message landing — it does not wait
 * for the trigger to pick the agent up.
 *
 * The transcript did not use it. It rendered a bubble only for `running`, which
 * a member reaches after the poll interval, a worker slot and Step 0. Between
 * those two moments the roster on the right knew someone was up, and the
 * conversation on the left showed nothing. That gap IS the reported symptom.
 *
 * This does not contradict the rule the two-pane layout was built on (see
 * TeamChatPanel.roster.test.tsx): a FINISHED turn still leaves nothing behind.
 * The rule is that `idle` leaves no trace — not that only `running` may show
 * one. `queued` and `stalled` are both live states with a message in flight.
 *
 * Deliberately reuses the roster's existing vocabulary
 * (`chat.team.activity.queued` / `.stalled` / `.waitingFor` / `.silentFor`)
 * rather than inventing transcript-only strings: two surfaces describing the
 * same state with different words is its own confusion, and it would have meant
 * new keys in ten locale files for no gain.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';

const getTeamChatMock = vi.fn();
const getEventLogMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: (...a: unknown[]) => getTeamChatMock(...a),
    getEventLog: (...a: unknown[]) => getEventLogMock(...a),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
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
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamChatPanel } from '../TeamChatPanel';
import { STATUS_TONES } from '@/lib/teamActivity';

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

const MESSAGE = {
  message_id: 'm1',
  from_agent: 'usr_usr_1',
  author_name: 'Bin',
  is_user: true,
  content: 'status?',
  created_at: '2026-07-30T09:00:00Z',
};

const RUNNING = {
  agent_id: 'a1',
  status: 'running' as const,
  phase: 'thinking',
  started_at: '2026-07-30T08:59:00Z',
};

const QUEUED = {
  agent_id: 'a2',
  status: 'queued' as const,
  queued_since: '2026-07-30T08:59:50Z',
  queued_count: 1,
};

const STALLED = {
  agent_id: 'a2',
  status: 'stalled' as const,
  started_at: '2026-07-30T08:50:00Z',
  last_signal_at: '2026-07-30T08:52:00Z',
};

const IDLE_WITH_TRACE = {
  agent_id: 'a2',
  status: 'idle' as const,
  started_at: '2026-07-30T08:50:00Z',
  finished_at: '2026-07-30T08:59:30Z',
  event_id: 'evt_1',
  steps: { items: [{ phase: 'thinking', at: '2026-07-30T08:50:10Z' }], dropped: 0 },
};

/** Transcript bubbles, identified by the aria-label prefix each state uses. */
function livenessBubbles(prefix: string) {
  return screen
    .queryAllByRole('button')
    .filter((b) => (b.getAttribute('aria-label') || '').includes(prefix));
}

async function renderRoom(activity: unknown[]) {
  getTeamChatMock.mockResolvedValue({
    success: true,
    messages: [MESSAGE],
    activity,
    lead_agent_id: 'a1',
  });
  const view = render(<TeamChatPanel teamId="t1" />);
  // Anchor on something that only exists once the fetch RESOLVED — the roster
  // comes from a synchronous store mock and would race the transcript.
  await screen.findByText(MESSAGE.content);
  return view;
}

beforeEach(() => {
  getTeamChatMock.mockReset();
  getEventLogMock.mockReset();
  getEventLogMock.mockResolvedValue({ success: true, timeline: [] });
  Element.prototype.scrollIntoView = () => {};
});

describe('TeamChatPanel · the transcript shows a sign of life', () => {
  test('a queued member gets a bubble, naming who and how long', async () => {
    await renderRoom([QUEUED]);

    const bubbles = await waitFor(() => {
      const found = livenessBubbles('chat.team.activity.queued');
      expect(found).toHaveLength(1);
      return found;
    });
    // The whole point is that the room says WHO is up, not just "something".
    expect(bubbles[0].getAttribute('aria-label')).toContain('Bruno');
    // ...and for how long it has been waiting, so a stuck queue is legible.
    // Scoped INSIDE the bubble: the members panel (when open) renders the same string, and an
    // unscoped query would pass on the roster alone — i.e. it would pass
    // against the exact bug this file exists to catch.
    expect(
      within(bubbles[0]).getByText(/chat\.team\.activity\.waitingFor/),
    ).toBeTruthy();
  });

  test('a running member still gets the typing bubble, unchanged', async () => {
    await renderRoom([RUNNING]);

    await waitFor(() =>
      expect(livenessBubbles('chat.team.typing')).toHaveLength(1),
    );
  });

  test('a stalled member is shown as stalled, not as typing', async () => {
    await renderRoom([STALLED]);

    const bubbles = await waitFor(() => {
      const found = livenessBubbles('chat.team.activity.stalled');
      expect(found).toHaveLength(1);
      return found;
    });
    // "no signal for 8 minutes" must never read as "still typing" — that is
    // the state the 90s staleness window exists to keep separate.
    expect(livenessBubbles('chat.team.typing')).toHaveLength(0);
    expect(
      within(bubbles[0]).getByText(/chat\.team\.activity\.silentFor/),
    ).toBeTruthy();
  });

  test('an idle member with a finished turn still leaves the transcript clean', async () => {
    // The rule the two-pane layout was built on, restated: a FINISHED turn
    // leaves nothing in the flow. Its trace lives in the roster, one click
    // away. Widening the filter must not have widened it to everything.
    await renderRoom([IDLE_WITH_TRACE]);

    await screen.findByText(MESSAGE.content);
    expect(livenessBubbles('chat.team.typing')).toHaveLength(0);
    expect(livenessBubbles('chat.team.activity.queued')).toHaveLength(0);
    expect(livenessBubbles('chat.team.activity.stalled')).toHaveLength(0);
  });

  test('the bubble uses the roster\'s semantic colour, not its own', async () => {
    // teamActivity.ts exists so the surfaces cannot disagree about what
    // "stalled" looks like. The first version of this bubble hard-coded its
    // palette and drew stalled as warning-amber while the roster drew the same
    // member error-red — two severities for one state, and colour is read
    // before words are.
    await renderRoom([STALLED]);
    const [bubble] = await waitFor(() => {
      const found = livenessBubbles('chat.team.activity.stalled');
      expect(found).toHaveLength(1);
      return found;
    });
    expect(bubble.getAttribute('style')).toContain(STATUS_TONES.stalled.color);
    expect(bubble.getAttribute('style')).not.toContain('var(--color-silicon)');
  });

  test('a queued member with no timestamp shows no half-written duration', async () => {
    // elapsedSince returns '' for a missing stamp; "waiting " with a blank tail
    // reads as a truncated string rather than an absent value.
    await renderRoom([{ agent_id: 'a2', status: 'queued' as const }]);
    const [bubble] = await waitFor(() => {
      const found = livenessBubbles('chat.team.activity.queued');
      expect(found).toHaveLength(1);
      return found;
    });
    expect(bubble.textContent).not.toContain('waitingFor');
  });

  test('a mixed room shows each member in its own state', async () => {
    await renderRoom([RUNNING, QUEUED]);

    await waitFor(() => {
      expect(livenessBubbles('chat.team.typing')).toHaveLength(1);
      expect(livenessBubbles('chat.team.activity.queued')).toHaveLength(1);
    });
  });
});
