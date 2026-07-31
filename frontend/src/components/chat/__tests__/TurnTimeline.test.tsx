/**
 * @file_name: TurnTimeline.test.tsx
 * @description: Render-shape tests for TurnTimeline. We don't poke at
 * exact pixels — instead pin the visible text and block ordering so a
 * future refactor that drops a block type or re-orders events fails
 * the build instead of silently changing UX.
 *
 * Since 2026-07-30 the answer tier (reply / native_output) renders in
 * the bubble via SegmentedReply — see SegmentedReply.test.tsx for those
 * assertions and turnTimeline.process.test.tsx for the process-only
 * contract. This file pins the process blocks themselves.
 */
import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TurnTimeline } from '../TurnTimeline';
import type { TurnEvent } from '@/types';

function ev(id: string, ts: number, type: TurnEvent['type'], extra: Partial<TurnEvent> = {}): TurnEvent {
  // Build a minimal valid TurnEvent of the requested type — tests only
  // assert on observable rendered output, not on the unused fields.
  switch (type) {
    case 'thinking':
      return { id, ts, type, content: 'reasoning here', ...extra } as TurnEvent;
    case 'tool_call':
      return {
        id, ts, type,
        tool_name: 'mcp__chat__get_chat_history',
        tool_input: { instance_id: 'chat_x' },
        ...extra,
      } as TurnEvent;
    case 'tool_output':
      return { id, ts, type, tool_name: 'x', output: 'ok', ...extra } as TurnEvent;
    case 'reply':
      return { id, ts, type, content: 'hi there', ...extra } as TurnEvent;
    case 'native_output':
      return { id, ts, type, content: 'native text', ...extra } as TurnEvent;
  }
}

describe('TurnTimeline', () => {
  test('renders nothing for empty events', () => {
    const { container } = render(<TurnTimeline events={[]} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders thinking block with label + preview', () => {
    render(
      <TurnTimeline
        events={[ev('t1', 1, 'thinking', { content: 'I should check chat history' })]}
      />
    );
    expect(screen.getByText(/Thinking/i)).not.toBeNull();
    expect(screen.getByText(/I should check chat history/)).not.toBeNull();
  });

  test('renders tool_call with friendly name (no MCP prefix)', () => {
    render(
      <TurnTimeline
        events={[ev('tc1', 1, 'tool_call', {
          tool_name: 'mcp__chat_module__get_chat_history',
          tool_input: { instance_id: 'chat_x' },
        })]}
      />
    );
    // Friendly tool name visible (last segment after mcp__module__)
    expect(screen.getByText('get_chat_history')).not.toBeNull();
    // MCP prefix should NOT bleed into the visible label
    // (queryByText returns null if not found — which is what we want)
    expect(screen.queryByText(/mcp__chat_module__/, { exact: false })).toBeNull();
  });

  test('renders process blocks in given order, answer tier filtered out', () => {
    const events: TurnEvent[] = [
      ev('e1', 1, 'thinking', { content: 'first thought' }),
      ev('e2', 2, 'tool_call', { tool_name: 'mcp__x__search_memory' }),
      ev('e3', 3, 'reply', { content: 'and here is the answer' }),
      ev('e4', 4, 'thinking', { content: 'follow up reasoning' }),
    ];
    const { container } = render(<TurnTimeline events={events} />);
    expect(screen.getByText(/first thought/)).not.toBeNull();
    expect(screen.getByText(/search_memory/)).not.toBeNull();
    expect(screen.getByText(/follow up reasoning/)).not.toBeNull();
    // The reply belongs to the bubble (SegmentedReply), not the timeline.
    expect(screen.queryByText(/and here is the answer/)).toBeNull();
    // Sanity check on block count: 3 process blocks, reply filtered.
    expect(container.querySelectorAll(':scope > div > *').length).toBe(3);
  });
});
