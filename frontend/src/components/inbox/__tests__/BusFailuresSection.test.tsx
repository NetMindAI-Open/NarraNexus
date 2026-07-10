/**
 * BusFailuresSection tests (upstream #52 recovery surface).
 *
 * Covers: hidden when clean, failure rows rendered, retry clears the row via
 * the retry API, and viewing the section consumes the matching unread
 * notices (message_bus_failure source only).
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getBusFailuresMock = vi.fn();
const retryBusFailureMock = vi.fn();
const getNoticesMock = vi.fn();
const markNoticeReadMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getBusFailures: (...a: unknown[]) => getBusFailuresMock(...a),
    retryBusFailure: (...a: unknown[]) => retryBusFailureMock(...a),
    getNotices: (...a: unknown[]) => getNoticesMock(...a),
    markNoticeRead: (...a: unknown[]) => markNoticeReadMock(...a),
  },
}));

import { BusFailuresSection } from '@/components/inbox/BusFailuresSection';

const FAILURE = {
  message_id: 'm1',
  channel_id: 'ch_team',
  from_agent: 'agent_peer',
  content: 'hello',
  retry_count: 3,
  last_error: 'OpenAI API key invalid',
  last_retry_at: '2026-07-03 06:00:00',
  message_created_at: '2026-07-03 05:00:00',
};

beforeEach(() => {
  getBusFailuresMock.mockReset();
  retryBusFailureMock.mockReset();
  getNoticesMock.mockReset();
  markNoticeReadMock.mockReset();
  getNoticesMock.mockResolvedValue({ success: true, notices: [], unread_count: 0 });
  markNoticeReadMock.mockResolvedValue({ success: true });
});

describe('BusFailuresSection', () => {
  test('renders nothing when the agent has no parked failures', async () => {
    getBusFailuresMock.mockResolvedValue({ success: true, failures: [] });
    render(<BusFailuresSection agentId="agent_a" />);
    await waitFor(() => expect(getBusFailuresMock).toHaveBeenCalledWith('agent_a'));
    expect(screen.queryByTestId('bus-failures-section')).toBeNull();
  });

  test('renders failure rows with the last error', async () => {
    getBusFailuresMock.mockResolvedValue({ success: true, failures: [FAILURE] });
    render(<BusFailuresSection agentId="agent_a" />);
    await screen.findByTestId('bus-failures-section');
    expect(screen.getByText(/OpenAI API key invalid/)).toBeTruthy();
    expect(screen.getByText(/ch_team/)).toBeTruthy();
  });

  test('retry calls the API and removes the row', async () => {
    getBusFailuresMock.mockResolvedValue({ success: true, failures: [FAILURE] });
    retryBusFailureMock.mockResolvedValue({ success: true });
    render(<BusFailuresSection agentId="agent_a" />);
    await screen.findByTestId('bus-failures-section');

    fireEvent.click(screen.getByRole('button'));
    await waitFor(() =>
      expect(retryBusFailureMock).toHaveBeenCalledWith('agent_a', 'm1'),
    );
    await waitFor(() =>
      expect(screen.queryByTestId('bus-failures-section')).toBeNull(),
    );
  });

  test('viewing failures consumes matching unread notices only', async () => {
    getBusFailuresMock.mockResolvedValue({ success: true, failures: [FAILURE] });
    getNoticesMock.mockResolvedValue({
      success: true,
      unread_count: 2,
      notices: [
        { message_id: 'n1', source: { type: 'message_bus_failure', id: 'ch_team' }, is_read: false },
        { message_id: 'n2', source: { type: 'other', id: 'x' }, is_read: false },
      ],
    });
    render(<BusFailuresSection agentId="agent_a" />);
    await screen.findByTestId('bus-failures-section');
    await waitFor(() => expect(markNoticeReadMock).toHaveBeenCalledTimes(1));
    expect(markNoticeReadMock).toHaveBeenCalledWith('n1');
  });
});
