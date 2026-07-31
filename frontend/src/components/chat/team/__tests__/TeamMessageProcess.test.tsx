/**
 * @file TeamMessageProcess.test.tsx
 * @description Per-message "view reasoning & tools" disclosure on team
 * transcript bubbles — single-chat parity. Pins: lazy fetch on first open
 * (never on render), one request per turn across toggles, and the terminal
 * timeline rendering once loaded.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { TeamMessageProcess } from '../TeamMessageProcess';

const getEventLogMock = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

vi.mock('@/lib/api', () => ({
  api: { getEventLog: (...a: unknown[]) => getEventLogMock(...a) },
}));

beforeEach(() => {
  getEventLogMock.mockReset();
  getEventLogMock.mockResolvedValue({
    success: true,
    timeline: [
      { type: 'thinking', content: 'pondering the answer' },
      { type: 'tool_call', tool_name: 'mcp__x__read_file', tool_input: { path: 'a.txt' } },
    ],
  });
});

test('renders the affordance without fetching', () => {
  render(<TeamMessageProcess agentId="agent_a" eventId="evt_1" />);
  expect(screen.getByText('chat.message.viewReasoning')).toBeTruthy();
  expect(getEventLogMock).not.toHaveBeenCalled();
});

test('first open fetches the event log and renders the timeline', async () => {
  render(<TeamMessageProcess agentId="agent_a" eventId="evt_1" />);
  fireEvent.click(screen.getByText('chat.message.viewReasoning'));

  await waitFor(() => {
    expect(screen.getByText('pondering the answer')).toBeTruthy();
  });
  expect(getEventLogMock).toHaveBeenCalledExactlyOnceWith('agent_a', 'evt_1');
  expect(screen.getByText('read_file')).toBeTruthy();
});

test('toggling closed and open again does not refetch', async () => {
  render(<TeamMessageProcess agentId="agent_a" eventId="evt_1" />);
  fireEvent.click(screen.getByText('chat.message.viewReasoning'));
  await waitFor(() => expect(screen.getByText('pondering the answer')).toBeTruthy());

  fireEvent.click(screen.getByText('chat.message.hideReasoning'));
  expect(screen.queryByText('pondering the answer')).toBeNull();

  fireEvent.click(screen.getByText('chat.message.viewReasoning'));
  await waitFor(() => expect(screen.getByText('pondering the answer')).toBeTruthy());
  expect(getEventLogMock).toHaveBeenCalledTimes(1);
});

test('an empty timeline degrades to the no-process note', async () => {
  getEventLogMock.mockResolvedValue({ success: true, timeline: [] });
  render(<TeamMessageProcess agentId="agent_a" eventId="evt_1" />);
  fireEvent.click(screen.getByText('chat.message.viewReasoning'));
  await waitFor(() => {
    expect(screen.getByText('chat.team.noProcess')).toBeTruthy();
  });
});

test('a failed fetch says so — and reopening retries instead of caching the failure', async () => {
  getEventLogMock.mockRejectedValueOnce(new Error('500'));
  render(<TeamMessageProcess agentId="agent_a" eventId="evt_1" />);

  fireEvent.click(screen.getByText('chat.message.viewReasoning'));
  // A network failure must not masquerade as "no process record".
  await waitFor(() => expect(screen.getByText('chat.team.detailLoadFailed')).toBeTruthy());
  expect(screen.queryByText('chat.team.noProcess')).toBeNull();

  // Close and reopen → a fresh request (the default mock now succeeds).
  fireEvent.click(screen.getByText('chat.message.hideReasoning'));
  fireEvent.click(screen.getByText('chat.message.viewReasoning'));
  await waitFor(() => expect(screen.getByText('pondering the answer')).toBeTruthy());
  expect(getEventLogMock).toHaveBeenCalledTimes(2);
});
