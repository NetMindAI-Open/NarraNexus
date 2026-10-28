/**
 * @file_name: TeamChatPanel.workspaceRefresh.test.tsx
 * @description: The workspace panel reloads after a file wipe.
 *
 * The loader is keyed on the transcript's message count, on the reasoning that
 * a turn which registered something has just landed in it. Clearing a team's
 * FILES is the one mutation that breaks that assumption: it empties the
 * workspace and its artifacts (they live in the same folder) while leaving the
 * transcript byte-identical, so the panel would go on listing rows the server
 * has already deleted — and every one of them 410s when clicked.
 *
 * Pinned here rather than in the dialog's own test because the bug is the
 * absence of a connection between two components, which neither one's tests
 * can see on its own.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const listTeamArtifactsMock = vi.fn();
const listTeamFilesMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: () => Promise.resolve({ success: true, messages: [], activity: [], lead_agent_id: null }),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: (...a: unknown[]) => listTeamArtifactsMock(...a),
    listTeamFiles: (...a: unknown[]) => listTeamFilesMock(...a),
    listTeamArtifactTurns: () => Promise.resolve({}),
  },
}));

let tick = 0;

// Stable identities: a fresh `notePatrol` per selector call would change the
// room's `refresh` every render and re-arm its poll effect without end.
const PATROL_BY_TEAM: Record<string, boolean> = {};
const NOTE_PATROL = () => {};

vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) =>
    select({ teams: TEAMS, patrolByTeam: PATROL_BY_TEAM, notePatrol: NOTE_PATROL }),
  useConfigStore: (select: (s: unknown) => unknown) =>
    select({ agents: AGENTS, displayName: 'Bin', userId: 'usr_1' }),
  useChatStore: (select: (s: unknown) => unknown) => select({ workspaceRefreshTick: tick }),
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

describe('team workspace refresh', () => {
  beforeEach(() => {
    tick = 0;
    listTeamArtifactsMock.mockReset().mockResolvedValue([]);
    listTeamFilesMock.mockReset().mockResolvedValue([]);
  });

  test('a bumped workspace tick re-fetches artifacts and files', async () => {
    const { rerender } = render(<TeamChatPanel teamId="t1" />);
    await waitFor(() => expect(listTeamArtifactsMock).toHaveBeenCalledTimes(1));

    // What clearing a team's files does: no new message, only the tick.
    tick = 1;
    rerender(<TeamChatPanel teamId="t1" />);

    await waitFor(() => expect(listTeamArtifactsMock).toHaveBeenCalledTimes(2));
    expect(listTeamFilesMock).toHaveBeenCalledTimes(2);
  });

  test('a steady tick does not re-fetch on every render', async () => {
    const { rerender } = render(<TeamChatPanel teamId="t1" />);
    await waitFor(() => expect(listTeamArtifactsMock).toHaveBeenCalledTimes(1));

    rerender(<TeamChatPanel teamId="t1" />);
    rerender(<TeamChatPanel teamId="t1" />);

    expect(listTeamArtifactsMock).toHaveBeenCalledTimes(1);
  });
});
