/**
 * @file_name: TeamChatPanel.mobileLayout.test.tsx
 * @description: Regression guard for GitHub #131 — on a phone-width viewport
 * the send button was clipped off-screen because the room's own flex column
 * had no `min-w-0`. MainLayout's ancestor wraps this panel in
 * `overflow-hidden`, so once a flex item is allowed to grow past the
 * viewport (the browser's default flex auto-min-size lets non-wrapping
 * descendants do that), the excess is clipped rather than scrolled — the
 * right-anchored send button ends up outside the visible area.
 *
 * jsdom has no layout engine, so this cannot assert the button's actual
 * on-screen position; it pins the CSS contract instead (`min-w-0` on the
 * room's flex column and on the root that holds it) so a revert of the fix
 * goes red.
 *
 * This pins the current implementation, not the only correct one: per the
 * flexbox spec `overflow-hidden` also zeroes an item's automatic min size
 * (ChatPanel uses that). If the room switches to `overflow-hidden`, update
 * these assertions with it.
 */
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: () =>
      Promise.resolve({ success: true, messages: [], activity: [], lead_agent_id: null }),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: () => Promise.resolve([]),
    listTeamFiles: () => Promise.resolve([]),
    listTeamArtifactTurns: () => Promise.resolve({}),
    sendTeamChat: () => Promise.resolve({ success: true }),
    uploadTeamChatAttachment: () => Promise.resolve({ success: true, attachment: null }),
  },
}));

// Stable identities: a fresh `notePatrol` per selector call would change the
// room's `refresh` every render and re-arm its poll effect without end (the
// run never finishes).
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

import { TeamChatPanel } from '../TeamChatPanel';

const AGENTS = [{ agent_id: 'a1', name: 'Ana Silva' }];
const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
];

describe('room shell stays within the viewport on a phone', () => {
  test('the room column carries min-w-0 so it cannot grow past its flex slot', () => {
    render(<TeamChatPanel teamId="t1" />);

    const shell = screen.getByTestId('team-room-shell');

    expect(shell.className.split(/\s+/)).toEqual(
      expect.arrayContaining(['flex-1', 'min-w-0']),
    );
    // The root that holds the column gets the same guard: when it is itself
    // a flex-row item, its content-based min width would otherwise win.
    expect(shell.parentElement?.className.split(/\s+/)).toContain('min-w-0');
  });
});
