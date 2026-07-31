/**
 * @file_name: TeamRosterPanel.test.tsx
 * @description: The roster's standing contract — what a team room shows about
 * its members without anyone clicking anything.
 *
 * The pinned decisions are all UX ones:
 *   1. EVERY member has a row, even one the activity poll never mentioned —
 *      "who is in this room" must not depend on who happens to be busy
 *   2. the row carries a live metric (elapsed / waiting / last run), because a
 *      bare status word is exactly what made a 25-minute run look like a hang
 *   3. detail is two-layered by data source: a running member shows the phase
 *      timeline the poll already carries, an idle one fetches its persisted
 *      event log — once per turn, not once per render
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TeamRosterPanel } from '../TeamRosterPanel';
import type { AgentInfo } from '@/types';
import type { TeamMemberActivity } from '@/types/teams';

const getEventLogMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: { getEventLog: (...a: unknown[]) => getEventLogMock(...a) },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const NOW = Date.parse('2026-07-28T09:00:00Z');

const MEMBERS: AgentInfo[] = [
  { agent_id: 'a1', name: 'Ana' },
  { agent_id: 'a2', name: 'Bo' },
  { agent_id: 'a3', name: 'Cy' },
];

const RUNNING: TeamMemberActivity = {
  agent_id: 'a1',
  status: 'running',
  phase: 'tool:mcp__x__read_file',
  started_at: '2026-07-28T08:58:00Z',
  last_signal_at: '2026-07-28T08:59:55Z',
};

const IDLE_WITH_RUN: TeamMemberActivity = {
  agent_id: 'a2',
  status: 'idle',
  started_at: '2026-07-28T08:50:00Z',
  finished_at: '2026-07-28T08:53:12Z',
  event_id: 'evt_9',
};

function renderPanel(props: Partial<React.ComponentProps<typeof TeamRosterPanel>> = {}) {
  return render(
    <TeamRosterPanel
      members={MEMBERS}
      activity={[IDLE_WITH_RUN, RUNNING]}
      leadAgentId="a1"
      now={NOW}
      expandedId={null}
      onToggle={() => {}}
      {...props}
    />,
  );
}

function rowOrder(): (string | null)[] {
  return screen
    .getAllByRole('button')
    .map((b) => b.getAttribute('data-testid'))
    .filter((id): id is string => !!id && id.startsWith('roster-row-'));
}

beforeEach(() => getEventLogMock.mockReset());

describe('TeamRosterPanel', () => {
  test('lists every member, running first, idle last', () => {
    renderPanel();
    // a3 was never mentioned by the activity poll — it is still a member.
    expect(rowOrder()).toEqual(['roster-row-a1', 'roster-row-a2', 'roster-row-a3']);
    expect(screen.getByText('Ana')).toBeTruthy();
    expect(screen.getByText('Cy')).toBeTruthy();
    // The default responder is marked so "who answers me" is visible.
    expect(screen.getByTitle('chat.team.leadTitle(Ana)')).toBeTruthy();
  });

  test('running row shows live action and elapsed', () => {
    renderPanel();
    // The MCP namespace is debug detail; the row says what the agent is doing.
    expect(screen.getByText('read_file')).toBeTruthy();
    expect(screen.getByText('2m00s')).toBeTruthy();
  });

  test('idle row shows last-run duration and recency', () => {
    renderPanel();
    expect(screen.getByText('chat.team.roster.lastRun(3m12s,6m48s)')).toBeTruthy();
    // A member that never ran says so rather than showing a blank metric.
    expect(screen.getByText('chat.team.roster.neverRan')).toBeTruthy();
  });

  test('clicking a row calls onToggle with its agent id', () => {
    const onToggle = vi.fn();
    renderPanel({ onToggle });
    fireEvent.click(screen.getByTestId('roster-row-a2'));
    expect(onToggle).toHaveBeenCalledWith('a2');
  });

  test('expanded idle member fetches event log detail once and renders rows', async () => {
    getEventLogMock.mockResolvedValue({
      success: true,
      event_id: 'evt_9',
      tool_calls: [],
      timeline: [{ type: 'tool_call', tool_name: 'mcp__x__read_file', tool_input: { path: 'x' } }],
    });

    const { rerender } = renderPanel({ expandedId: 'a2' });
    expect(await screen.findByText('read_file')).toBeTruthy();
    expect(getEventLogMock).toHaveBeenCalledWith('a2', 'evt_9');

    // A 3s poll re-renders the panel with identical data — the turn's detail is
    // already in hand, so it must not be refetched.
    rerender(
      <TeamRosterPanel
        members={MEMBERS}
        activity={[IDLE_WITH_RUN, RUNNING]}
        leadAgentId="a1"
        now={NOW + 1000}
        expandedId="a2"
        onToggle={() => {}}
      />,
    );
    expect(getEventLogMock).toHaveBeenCalledTimes(1);
  });

  test('expanded running member opens the live terminal card, no event-log fetch', () => {
    const { container } = renderPanel({
      expandedId: 'a1',
      activity: [
        {
          ...RUNNING,
          phase: 'thinking',
          event_id: 'evt_live',
        },
        IDLE_WITH_RUN,
      ],
    });

    // v2: the detail is a mini ProcessPanel fed by the run-observation
    // channel; before the socket delivers, the card shows the honest
    // "starting up" fallback rather than pretending it knows more.
    expect(screen.getByTestId('member-panel-a1')).toBeTruthy();
    expect(screen.getByText('chat.execution.startingUp')).toBeTruthy();
    // The column breathes: an open member widens the aside for the terminal.
    expect(container.querySelector('aside')?.className).toContain('430px');
    // A running turn's process is not in the event log yet — it is written at
    // the end of the turn — so there is nothing to fetch.
    expect(getEventLogMock).not.toHaveBeenCalled();
  });

  test('zero members renders the empty state', () => {
    const onOpenSettings = vi.fn();
    render(
      <TeamRosterPanel
        members={[]}
        activity={[]}
        leadAgentId={null}
        now={NOW}
        expandedId={null}
        onToggle={() => {}}
        onOpenSettings={onOpenSettings}
      />,
    );
    expect(screen.getByText('chat.team.noAgents')).toBeTruthy();
    fireEvent.click(screen.getByText('chat.team.teamSettings'));
    expect(onOpenSettings).toHaveBeenCalled();
  });
});
