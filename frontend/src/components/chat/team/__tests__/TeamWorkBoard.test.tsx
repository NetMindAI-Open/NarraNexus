/**
 * @file_name: TeamWorkBoard.test.tsx
 * @description: The work board's contract with the user.
 *
 * The roster says who is busy right now; the board says what the team owes.
 * Pinned here are the two states that are the user's business specifically:
 *   1. `paused` is VISIBLE and resumable — a stopped task must not look
 *      deleted, and patrol deliberately will not un-park it (that would let a
 *      sweep undo the owner's stop)
 *   2. patrol's trace is shown here and nowhere else, because a healthy sweep
 *      says nothing in the room by design
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TeamWorkBoard } from '../TeamWorkBoard';
import { useTeamsStore } from '@/stores';

const getBoardMock = vi.fn();
const resumeMock = vi.fn();
const setPatrolMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamWorkBoard: (...a: unknown[]) => getBoardMock(...a),
    resumeTeamWorkItem: (...a: unknown[]) => resumeMock(...a),
    setTeamPatrol: (...a: unknown[]) => setPatrolMock(...a),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const NOW = Date.parse('2026-08-10T10:00:00Z');

function board(over: Record<string, unknown> = {}) {
  return {
    success: true,
    items: [],
    last_patrol_at: null,
    patrol_enabled: true,
    ...over,
  };
}

const LIVE = {
  item_id: 'wi_1', kind: 'task', title: 'OCR the scans', assignee_id: 'a2',
  assignee_name: 'Bruno', status: 'in_progress',
};
const PARKED = {
  item_id: 'wi_2', kind: 'task', title: 'draft summary', assignee_id: null,
  assignee_name: null, status: 'paused',
};

beforeEach(() => {
  useTeamsStore.setState({ patrolByTeam: {}, patrolPendingUntil: {}, patrolInFlight: {} });
  getBoardMock.mockReset();
  resumeMock.mockReset();
  resumeMock.mockResolvedValue({ success: true });
  setPatrolMock.mockReset();
  setPatrolMock.mockResolvedValue({ success: true });
});

describe('TeamWorkBoard', () => {
  test('an empty board renders nothing at all', async () => {
    getBoardMock.mockResolvedValue(board());
    const { container } = render(<TeamWorkBoard teamId="t1" now={NOW} />);

    await waitFor(() => expect(getBoardMock).toHaveBeenCalled());
    // Not an empty-state header: permanent chrome for an absent thing is the
    // debt this room's design keeps paying off.
    expect(container.textContent).toBe('');
  });

  test('items show owner and status', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('OCR the scans')).toBeTruthy();
    expect(screen.getByText(/Bruno/)).toBeTruthy();
  });

  test('unclaimed work says so', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText(/chat\.team\.board\.unclaimed/)).toBeTruthy();
  });

  test('a parked item is visible and offers resume', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    // Visible: a stopped task must not look deleted.
    expect(await screen.findByText('draft summary')).toBeTruthy();
    expect(screen.getByTestId('work-resume-wi_2')).toBeTruthy();
  });

  test('a live item offers no resume', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    await screen.findByText('OCR the scans');
    expect(screen.queryByTestId('work-resume-wi_1')).toBeNull();
  });

  test('resuming calls through and refreshes', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('work-resume-wi_2'));

    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_2'));
    // Refetched, so the row reflects its new state rather than a local guess.
    await waitFor(() => expect(getBoardMock.mock.calls.length).toBeGreaterThan(1));
  });

  test('a hand-off card shows sender → recipients, not the message text', async () => {
    const HANDOFF = {
      item_id: 'msg_x', kind: 'handoff', title: '', status: 'in_progress',
      source_name: 'Ada', assignee_names: ['Bruno', 'Cara'],
      item_ids: ['wi_1', 'wi_2'],
    };
    getBoardMock.mockResolvedValue(board({ items: [HANDOFF] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    // Sender and both recipients are on one card.
    const card = await screen.findByTestId('work-item-msg_x');
    expect(card.textContent).toContain('Ada');
    expect(card.textContent).toContain('Bruno');
    expect(card.textContent).toContain('Cara');
    // The "awaiting reply" label, not a status pretending someone spoke.
    expect(card.textContent).toContain('chat.team.board.awaitingReply');
  });

  test('resuming a paused hand-off resumes every parked row', async () => {
    const PARKED_HANDOFF = {
      item_id: 'msg_x', kind: 'handoff', title: '', status: 'paused',
      source_name: 'Ada', assignee_names: ['Bruno', 'Cara'],
      item_ids: ['wi_1', 'wi_2'], paused_item_ids: ['wi_1', 'wi_2'],
    };
    getBoardMock.mockResolvedValue(board({ items: [PARKED_HANDOFF] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('work-resume-msg_x'));

    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_1'));
    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_2'));
  });

  test('a half-paused hand-off still offers resume, and only for the parked row', async () => {
    // Aggregate status is in_progress (one row still active), but a parked row
    // remains — the card must not lose its resume button, and must resume only
    // the parked id, not the live one.
    const HALF = {
      item_id: 'msg_x', kind: 'handoff', title: '', status: 'in_progress',
      source_name: 'Ada', assignee_names: ['Bruno', 'Cara'],
      item_ids: ['wi_1', 'wi_2'], paused_item_ids: ['wi_2'],
    };
    getBoardMock.mockResolvedValue(board({ items: [HALF] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('work-resume-msg_x'));

    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_2'));
    expect(resumeMock).not.toHaveBeenCalledWith('t1', 'wi_1');
  });

  test('a resume that fails on one row keeps the card resumable', async () => {
    // One row rejects; `allSettled` still attempts the rest, and because the
    // failed row is re-reported as paused, its resume affordance survives.
    const HANDOFF = {
      item_id: 'msg_x', kind: 'handoff', title: '', status: 'paused',
      source_name: 'Ada', assignee_names: ['Bruno', 'Cara'],
      item_ids: ['wi_1', 'wi_2'], paused_item_ids: ['wi_1', 'wi_2'],
    };
    getBoardMock.mockResolvedValue(board({ items: [HANDOFF] }));
    resumeMock.mockImplementation((_t: string, id: string) =>
      id === 'wi_2' ? Promise.reject(new Error('nope')) : Promise.resolve({ success: true }),
    );
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('work-resume-msg_x'));

    // Both attempted despite one failing, and the button is still there.
    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_1'));
    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_2'));
    expect(await screen.findByTestId('work-resume-msg_x')).toBeTruthy();
  });

  test('patrol trace: never swept', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE], last_patrol_at: null }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolPending')).toBeTruthy();
  });

  test('patrol trace: elapsed since the last sweep', async () => {
    getBoardMock.mockResolvedValue(board({
      items: [LIVE], last_patrol_at: '2026-08-10T09:48:00Z',
    }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolledAgo(12m)')).toBeTruthy();
  });

  test('patrol trace: switched off', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE], patrol_enabled: false }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolOff')).toBeTruthy();
  });
});

describe('TeamWorkBoard · patrol switch moved out (2026-09-03)', () => {
  test('the board carries no switch — it lives in the management tab', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolPending')).toBeTruthy();
    expect(screen.queryByTestId('patrol-toggle')).toBeNull();
    expect(setPatrolMock).not.toHaveBeenCalled();
  });

  test('the trace text reads the store, so a flip elsewhere shows at once', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE], patrol_enabled: true }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);
    expect(await screen.findByText('chat.team.board.patrolPending')).toBeTruthy();

    act(() => useTeamsStore.getState().notePatrol('t1', false));

    expect(await screen.findByText('chat.team.board.patrolOff')).toBeTruthy();
  });

  test('an empty board is absent even with patrol OFF', async () => {
    getBoardMock.mockResolvedValue(board({ items: [], patrol_enabled: false }));
    const { container } = render(<TeamWorkBoard teamId="t1" now={NOW} />);

    await waitFor(() => expect(getBoardMock).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });
});
