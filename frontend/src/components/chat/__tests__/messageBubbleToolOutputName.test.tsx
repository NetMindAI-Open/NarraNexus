/**
 * Historical event-log timelines store tool_output entries without a
 * tool_name. The bubble's disclosure must show the name inherited from the
 * preceding tool_call — never a literal placeholder word.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/api', () => ({
  api: {
    getEventLog: vi.fn().mockResolvedValue({
      success: true,
      thinking: '',
      tool_calls: [],
      timeline: [
        { type: 'tool_call', tool_name: 'mcp__fs__read_file', tool_input: { path: 'a' } },
        { type: 'tool_output', tool_output: 'file body' },
      ],
    }),
  },
}));

import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

const message: ChatMessage = {
  id: 'm1',
  role: 'assistant',
  content: 'done',
  timestamp: 0,
};

describe('MessageBubble tool-output naming', () => {
  it('an unnamed tool_output inherits the preceding call name — no placeholder', async () => {
    render(
      <MemoryRouter>
        <MessageBubble message={message} eventId="ev1" agentId="a1" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /reasoning/i }));
    // Both the call row and the output row carry the short tool name.
    await waitFor(() => {
      expect(screen.getAllByText('read_file').length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.queryByText('unknown')).toBeNull();
  });
});
