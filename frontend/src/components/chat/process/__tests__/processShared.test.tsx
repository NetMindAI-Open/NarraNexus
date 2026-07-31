/**
 * ProcessEventRows — the shared terminal-style rows (thinking / tool
 * call / tool output) reused by ProcessPanel and the team roster's
 * member detail. Smoke test: each row type renders with the friendly
 * tool name.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProcessEventRows } from '../processShared';
import type { TurnEvent } from '@/types';

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: 'pondering' },
  {
    id: 'c1', ts: 2, type: 'tool_call', tool_name: 'mcp__x__read_file',
    tool_input: { path: '/tmp/a' }, pending: false,
  },
  {
    id: 'o1', ts: 3, type: 'tool_output', tool_name: 'mcp__x__read_file',
    output: '42 lines',
  },
];

describe('ProcessEventRows', () => {
  it('renders thinking, tool call (friendly name) and output rows', () => {
    render(<ProcessEventRows process={events} />);
    expect(screen.getByText('pondering')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.getByText('42 lines')).toBeInTheDocument();
  });
});
