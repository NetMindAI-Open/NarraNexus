/**
 * InnerThoughtCard tests — inner-thought (activity) card.
 *
 * Covers: summary + source-labelled header, lazy event-log fetch on expand,
 * step rendering, legacy fallback, distinct empty vs load-failed states, and
 * no expander when the activity has no event_id.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getEventLogMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getEventLog: (...a: unknown[]) => getEventLogMock(...a) },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { InnerThoughtCard } from '@/components/chat/InnerThoughtCard';

const baseItem = {
  id: 'a1',
  role: 'assistant' as const,
  content: 'Executed a background job',
  timestamp: Date.parse('2026-07-03T03:29:20.000Z'),
  source: 'history' as const,
  messageType: 'activity',
  workingSource: 'job',
  eventId: 'evt_1',
};

beforeEach(() => getEventLogMock.mockReset());

describe('InnerThoughtCard', () => {
  test('renders summary and source-labelled header', () => {
    render(<InnerThoughtCard item={baseItem} agentId="agent_a" />);
    expect(screen.getByTestId('inner-thought-card')).toBeTruthy();
    expect(screen.getByText('Executed a background job')).toBeTruthy();
    expect(screen.getByText('chat.inner.source.job')).toBeTruthy();
  });

  test('IM channels show their brand name; category sources show a label', () => {
    const { rerender } = render(
      <InnerThoughtCard item={{ ...baseItem, workingSource: 'wechat' }} agentId="a" />,
    );
    expect(screen.getByText('WeChat')).toBeTruthy();
    rerender(<InnerThoughtCard item={{ ...baseItem, workingSource: 'lark' }} agentId="a" />);
    expect(screen.getByText('chat.inner.source.lark')).toBeTruthy();
    rerender(<InnerThoughtCard item={{ ...baseItem, workingSource: 'message_bus' }} agentId="a" />);
    expect(screen.getByText('chat.inner.source.collaboration')).toBeTruthy();
  });

  test('unknown source falls back to the generic activity label', () => {
    render(<InnerThoughtCard item={{ ...baseItem, workingSource: 'mystery' }} agentId="a" />);
    expect(screen.getByText('chat.inner.source.activity')).toBeTruthy();
  });

  test('expanding lazily fetches the event log and renders steps', async () => {
    getEventLogMock.mockResolvedValue({
      success: true,
      event_id: 'evt_1',
      tool_calls: [],
      timeline: [
        { type: 'thinking', content: 'considering the request' },
        { type: 'tool_call', tool_name: 'web_search', tool_input: {} },
      ],
    });
    render(<InnerThoughtCard item={baseItem} agentId="agent_a" />);
    fireEvent.click(screen.getByText('chat.inner.viewLoop'));
    await waitFor(() => expect(getEventLogMock).toHaveBeenCalledWith('agent_a', 'evt_1'));
    expect(await screen.findByText('considering the request')).toBeTruthy();
    expect(screen.getByText('web_search')).toBeTruthy();
  });

  test('falls back to thinking + tool_calls when timeline is absent', async () => {
    getEventLogMock.mockResolvedValue({
      success: true,
      event_id: 'evt_1',
      thinking: 'legacy thinking text',
      tool_calls: [{ tool_name: 'legacy_tool', tool_input: {} }],
    });
    render(<InnerThoughtCard item={baseItem} agentId="agent_a" />);
    fireEvent.click(screen.getByText('chat.inner.viewLoop'));
    expect(await screen.findByText('legacy thinking text')).toBeTruthy();
    expect(screen.getByText('legacy_tool')).toBeTruthy();
  });

  test('empty event log shows the empty state', async () => {
    getEventLogMock.mockResolvedValue({ success: true, event_id: 'evt_1', tool_calls: [] });
    render(<InnerThoughtCard item={baseItem} agentId="agent_a" />);
    fireEvent.click(screen.getByText('chat.inner.viewLoop'));
    expect(await screen.findByText('chat.inner.empty')).toBeTruthy();
  });

  test('a failed fetch shows load-failed, distinct from empty', async () => {
    getEventLogMock.mockImplementationOnce(async () => {
      throw new Error('boom');
    });
    render(<InnerThoughtCard item={baseItem} agentId="agent_a" />);
    fireEvent.click(screen.getByText('chat.inner.viewLoop'));
    expect(await screen.findByText('chat.inner.loadFailed')).toBeTruthy();
  });

  test('no expander when the activity has no event_id', () => {
    render(<InnerThoughtCard item={{ ...baseItem, eventId: undefined }} agentId="a" />);
    expect(screen.queryByText('chat.inner.viewLoop')).toBeNull();
  });
});
