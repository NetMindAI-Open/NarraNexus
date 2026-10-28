/**
 * @file_name: TeamMemberPanel.stop.test.tsx
 * @description: The owner's stop button on a running member.
 *
 * The pinned decisions:
 *   1. the click is acknowledged IMMEDIATELY ("stopping…"), before anything
 *      has actually stopped — the incident this feature exists for was 8
 *      minutes of silence, so the acknowledgement is the feature
 *   2. a stop that never landed (403, run gone, network) must not leave the
 *      button stuck on "stopping" forever
 *   3. the button belongs to live members with a run id — there is nothing to
 *      stop on an idle row, and a dead button is worse than no button
 *   4. a cancelled run says so; it must never read as "completed"
 *   5. a stop is scoped to the run it was aimed at — the next turn starts with
 *      a clean button
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TeamMemberPanel } from '../TeamMemberPanel';
import type { TeamMemberActivity } from '@/types/teams';

const cancelRunMock = vi.fn();
const observationMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    cancelRun: (...a: unknown[]) => cancelRunMock(...a),
    getEventLog: vi.fn().mockResolvedValue({ success: true, event_log: [] }),
  },
}));

vi.mock('@/hooks/useRunObservation', () => ({
  useRunObservation: () => observationMock(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const NOW = Date.parse('2026-08-07T09:00:00Z');

const RUNNING: TeamMemberActivity = {
  agent_id: 'a1',
  status: 'running',
  phase: 'thinking',
  started_at: '2026-08-07T08:58:00Z',
  last_signal_at: '2026-08-07T08:59:55Z',
  event_id: 'evt_run_1',
};

const IDLE: TeamMemberActivity = {
  agent_id: 'a1',
  status: 'idle',
  finished_at: '2026-08-07T08:50:00Z',
  event_id: 'evt_old',
};

function baseObservation(over: Record<string, unknown> = {}) {
  return {
    status: 'live',
    endState: null,
    events: [],
    steps: [],
    startedAt: null,
    errorMessage: null,
    opsCount: 0,
    ...over,
  };
}

beforeEach(() => {
  cancelRunMock.mockReset();
  cancelRunMock.mockResolvedValue({ success: true, already_settled: false });
  observationMock.mockReset();
  observationMock.mockReturnValue(baseObservation());
});

describe('TeamMemberPanel stop', () => {
  test('a running member offers a stop button', () => {
    render(<TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />);
    expect(screen.getByTestId('member-stop-a1')).toBeTruthy();
  });

  test('an idle member offers none', () => {
    render(<TeamMemberPanel activity={IDLE} name="Ana" now={NOW} open />);
    expect(screen.queryByTestId('member-stop-a1')).toBeNull();
  });

  test('a live member without a run id offers none', () => {
    render(
      <TeamMemberPanel
        activity={{ ...RUNNING, event_id: null }}
        name="Ana"
        now={NOW}
        open
      />,
    );
    expect(screen.queryByTestId('member-stop-a1')).toBeNull();
  });

  test('the click is acknowledged before anything has stopped', async () => {
    // A request that never resolves — the acknowledgement must not wait on it.
    cancelRunMock.mockReturnValue(new Promise(() => {}));
    render(<TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />);

    fireEvent.click(screen.getByTestId('member-stop-a1'));

    await waitFor(() =>
      expect(screen.getByText('chat.team.roster.stopping')).toBeTruthy(),
    );
    expect(cancelRunMock).toHaveBeenCalledWith('evt_run_1');
    expect(screen.getByTestId('member-stop-a1')).toHaveProperty('disabled', true);
  });

  test('a stop that never landed surfaces instead of hanging', async () => {
    cancelRunMock.mockRejectedValue(new Error('403'));
    render(<TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />);

    fireEvent.click(screen.getByTestId('member-stop-a1'));

    await waitFor(() =>
      expect(screen.getByText('chat.team.roster.stopFailed')).toBeTruthy(),
    );
    // Back to an actionable button rather than a permanent spinner.
    expect(screen.getByTestId('member-stop-a1')).toHaveProperty('disabled', false);
  });

  test('a cancelled run reads as stopped, not completed', () => {
    observationMock.mockReturnValue(
      baseObservation({ status: 'ended', endState: 'cancelled' }),
    );
    render(<TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />);
    expect(screen.getByText('chat.team.roster.stopped')).toBeTruthy();
  });

  test('a completed run does not claim it was stopped', () => {
    observationMock.mockReturnValue(
      baseObservation({ status: 'ended', endState: 'completed' }),
    );
    render(<TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />);
    expect(screen.queryByText('chat.team.roster.stopped')).toBeNull();
  });

  test('the next run gets a clean button', async () => {
    cancelRunMock.mockReturnValue(new Promise(() => {}));
    const { rerender } = render(
      <TeamMemberPanel activity={RUNNING} name="Ana" now={NOW} open />,
    );
    fireEvent.click(screen.getByTestId('member-stop-a1'));
    await waitFor(() =>
      expect(screen.getByText('chat.team.roster.stopping')).toBeTruthy(),
    );

    // A new turn on the same member — the previous run's pending stop must
    // not carry over and grey out a button for a run nobody asked to stop.
    rerender(
      <TeamMemberPanel
        activity={{ ...RUNNING, event_id: 'evt_run_2' }}
        name="Ana"
        now={NOW}
        open
      />,
    );
    expect(screen.getByText('chat.team.roster.stop')).toBeTruthy();
    expect(screen.getByTestId('member-stop-a1')).toHaveProperty('disabled', false);
  });
});
