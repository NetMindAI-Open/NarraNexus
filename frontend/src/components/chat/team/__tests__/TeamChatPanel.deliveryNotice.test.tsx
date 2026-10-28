/**
 * @file_name: TeamChatPanel.deliveryNotice.test.tsx
 * @description: The room says when a turn delivered nothing.
 *
 * Backend counterpart: message_bus/delivery_notice.py. Two new platform lines
 * reach the transcript — `system_undelivered` (the turn produced nothing) and
 * `system_delivery_failed` (the reply existed, posting it failed). Both must
 * render as ROOM-level lines: dressing either as the agent's own bubble would
 * put words in its mouth, and the second one would attribute OUR write failure
 * to the agent.
 *
 * The English `content` is a fallback for text-only consumers (logs, exports),
 * never the thing a reader sees — the database cannot know their language. So
 * these tests also pin that the raw sentence does NOT reach the transcript.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const getTeamChatMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: (...a: unknown[]) => getTeamChatMock(...a),
    getEventLog: () => Promise.resolve({ success: true, timeline: [] }),
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

const AGENTS = [{ agent_id: 'a1', name: 'Ana' }];

const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
];

const USER_MESSAGE = {
  message_id: 'm1',
  from_agent: 'usr_usr_1',
  author_name: 'Bin',
  is_user: true,
  content: 'status?',
  created_at: '2026-08-13T09:00:00Z',
};

const UNDELIVERED = {
  message_id: 'm2',
  from_agent: 'a1',
  author_name: 'Ana',
  is_user: false,
  content: 'This turn ended without delivering a reply.',
  msg_type: 'system_undelivered',
  // Sent by the server with every message: whether this line is the platform
  // narrating itself. The transcript used to decide that from its own copy of
  // PLATFORM_MSG_TYPES — a list that had already fallen two types behind, these
  // two among them.
  is_platform: true,
  created_at: '2026-08-13T09:00:10Z',
};

const DELIVERY_FAILED = {
  message_id: 'm3',
  from_agent: 'a1',
  author_name: 'Ana',
  is_user: false,
  content: 'The reply could not be posted to this conversation. (DB is down)',
  msg_type: 'system_delivery_failed',
  is_platform: true,
  created_at: '2026-08-13T09:00:20Z',
};

/** `anchor` must only exist once getTeamChat RESOLVED — see the roster test. */
async function renderRoom(messages: unknown[]) {
  getTeamChatMock.mockResolvedValue({
    success: true,
    messages: [USER_MESSAGE, ...messages],
    activity: [],
    lead_agent_id: 'a1',
  });
  const view = render(<TeamChatPanel teamId="t1" />);
  await screen.findByText(USER_MESSAGE.content);
  return view;
}

beforeEach(() => {
  getTeamChatMock.mockReset();
  Element.prototype.scrollIntoView = () => {};
});

describe('delivery notices in the team transcript', () => {
  test('a silent turn shows a room-level line naming the member', async () => {
    await renderRoom([UNDELIVERED]);

    const line = await screen.findByTestId('undelivered-notice-m2');
    expect(line).toHaveTextContent('chat.team.undeliveredNotice(Ana)');
    // The English fallback is for text-only consumers, not for readers.
    expect(screen.queryByText(UNDELIVERED.content)).toBeNull();
  });

  test('a failed post shows its own line and keeps the reason on hover', async () => {
    await renderRoom([DELIVERY_FAILED]);

    const line = await screen.findByTestId('delivery-failed-notice-m3');
    expect(line).toHaveTextContent('chat.team.deliveryFailedNotice(Ana)');
    // Why it failed stays one hover away: quiet in the transcript, available
    // to whoever is debugging.
    expect(line.querySelector('[title]')?.getAttribute('title')).toContain('DB is down');
  });

  test('the two are told apart, not collapsed into one line', async () => {
    await renderRoom([UNDELIVERED, DELIVERY_FAILED]);

    // "the agent said nothing" and "we lost what it said" are different facts
    // and different remedies; one shared line would hide which happened.
    expect(await screen.findByTestId('undelivered-notice-m2')).toBeTruthy();
    expect(screen.getByTestId('delivery-failed-notice-m3')).toBeTruthy();
  });

  test('neither is rendered as the member speaking', async () => {
    await renderRoom([UNDELIVERED, DELIVERY_FAILED]);

    // Centred, like the stop and bulletin notices — a member's message is a
    // left/right-aligned bubble with an avatar. Not asserted by looking for
    // the author name: the standing roster prints every member's name as
    // chrome, so its presence says nothing about the transcript.
    for (const id of ['undelivered-notice-m2', 'delivery-failed-notice-m3']) {
      expect((await screen.findByTestId(id)).className).toContain('justify-center');
    }
  });
});
