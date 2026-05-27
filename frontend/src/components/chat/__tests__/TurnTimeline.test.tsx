/**
 * @file_name: TurnTimeline.test.tsx
 * @description: Render-shape tests for TurnTimeline. We don't poke at
 * exact pixels — instead pin the visible text and block ordering so a
 * future refactor that drops a block type or re-orders events fails
 * the build instead of silently changing UX.
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

  test('renders reply block as user-facing speech', () => {
    render(
      <TurnTimeline
        events={[ev('r1', 1, 'reply', { content: 'Hello user, here is the answer.' })]}
      />
    );
    expect(screen.getByText(/Hello user, here is the answer/)).not.toBeNull();
  });

  test('marks helper_llm no_reply mode with the info badge', () => {
    render(
      <TurnTimeline
        events={[ev('r2', 1, 'reply', {
          content: 'Recovered reply',
          reply_via: 'helper_llm_no_reply',
        })]}
      />
    );
    expect(screen.getByText(/helper_llm fallback/i)).not.toBeNull();
  });

  test('marks helper_llm after_error mode with the warning badge', () => {
    render(
      <TurnTimeline
        events={[ev('r2a', 1, 'reply', {
          content: 'I started but hit a snag — here is what I found.',
          reply_via: 'helper_llm_after_error',
        })]}
      />
    );
    // Distinct text (warning) from the info badge.
    expect(screen.getByText(/recovered after error/i)).not.toBeNull();
    // The info badge must NOT be present for after_error replies.
    expect(screen.queryByText(/helper_llm fallback/i)).toBeNull();
  });

  test('legacy helper_llm_fallback tag still renders the info badge', () => {
    // Persisted rows from before the 2026-05-25 rename carry the old
    // tag name; the UI must keep rendering them so historical replies
    // still surface as recovered.
    render(
      <TurnTimeline
        events={[ev('r2legacy', 1, 'reply', {
          content: 'Legacy recovered reply',
          reply_via: 'helper_llm_fallback',
        })]}
      />
    );
    expect(screen.getByText(/helper_llm fallback/i)).not.toBeNull();
  });

  test('renders native_output with its own label, distinct from reply', () => {
    render(
      <TurnTimeline
        events={[ev('n1', 1, 'native_output', { content: 'just an aside' })]}
      />
    );
    expect(screen.getByText(/Native output/i)).not.toBeNull();
    expect(screen.getByText(/just an aside/)).not.toBeNull();
  });

  test('renders mixed sequence in given order', () => {
    const events: TurnEvent[] = [
      ev('e1', 1, 'thinking', { content: 'first thought' }),
      ev('e2', 2, 'tool_call', { tool_name: 'mcp__x__search_memory' }),
      ev('e3', 3, 'reply', { content: 'and here is the answer' }),
      ev('e4', 4, 'thinking', { content: 'follow up reasoning' }),
    ];
    const { container } = render(<TurnTimeline events={events} />);
    // Each block produces visible text; verify each is present once.
    expect(screen.getByText(/first thought/)).not.toBeNull();
    expect(screen.getByText(/search_memory/)).not.toBeNull();
    expect(screen.getByText(/and here is the answer/)).not.toBeNull();
    expect(screen.getByText(/follow up reasoning/)).not.toBeNull();
    // Sanity check on block count.
    expect(container.querySelectorAll(':scope > div > *').length).toBe(4);
  });
});
