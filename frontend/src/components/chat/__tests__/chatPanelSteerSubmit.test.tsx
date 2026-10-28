/**
 * handleSubmit's mid-run "steer" branch — the wiring the store/wsManager unit
 * tests don't cover: which of steer() / run() actually fires for each state,
 * and that the optimistic bubble is only cleared/created when it should be.
 *
 * The button's `disabled` and the function's early-return must agree; this test
 * is what would have caught them drifting apart (a lit-but-dead send button).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Controllable WS hook: steer()/run() are spies; isLoading is flipped per test.
const h = vi.hoisted(() => ({
  steer: vi.fn<(a: string, c: string, id: string) => boolean>(() => true),
  run: vi.fn(),
  isLoading: false,
}));

vi.mock('@/hooks', async (importOriginal) => {
  // Keep every other hook (useDismissOnOutside, etc.) real; override only the
  // two ChatPanel drives so we can control isLoading and spy on steer()/run().
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAgentWebSocket: () => ({
      run: h.run,
      reconnect: vi.fn(),
      stop: vi.fn(),
      steer: h.steer,
      isLoading: h.isLoading,
    }),
    useFastMode: () => [false, vi.fn()],
  };
});

vi.mock('@/lib/api', () => ({
  api: {
    getSimpleChatHistory: vi.fn().mockResolvedValue({ success: true, messages: [], total_count: 0 }),
    getTranscriptionAvailability: vi.fn().mockResolvedValue({ available: false, reason: '' }),
    uploadAttachment: vi.fn(),
  },
}));

import { ChatPanel } from '../ChatPanel';
import { useConfigStore, useChatStore } from '@/stores';

const AGENT = 'a1';

function seedStreaming(steerable: boolean) {
  const cs = useChatStore.getState();
  cs.startStreaming(AGENT);
  // run_started is what carries steerability to the store.
  cs.processMessage(AGENT, { type: 'run_started', run_id: 'r1', steerable });
}

function typeInComposer(value: string) {
  const box = screen.getByRole('textbox');
  fireEvent.change(box, { target: { value } });
  return box;
}

const bubbles = () => useChatStore.getState().agentSessions[AGENT]?.messages ?? [];

describe('ChatPanel handleSubmit — mid-run steer wiring', () => {
  beforeEach(() => {
    h.steer.mockClear();
    h.run.mockClear();
    h.steer.mockReturnValue(true);
    h.isLoading = false;
    useChatStore.setState({ agentSessions: {}, activeAgentId: AGENT });
    useConfigStore.setState({
      agentId: AGENT,
      userId: 'u1',
      agents: [{ agent_id: AGENT, name: 'Analyst' } as never],
    });
  });

  it('steerable run + text → calls steer(), never run(), and folds an optimistic bubble', () => {
    h.isLoading = true;
    seedStreaming(true);
    render(<ChatPanel />, { wrapper: MemoryRouter });
    typeInComposer('also send the summary');
    // The steer send button only exists on a steerable run.
    act(() => {
      fireEvent.click(screen.getByTitle('Send into this run (Enter)'));
    });
    expect(h.steer).toHaveBeenCalledTimes(1);
    expect(h.steer.mock.calls[0][0]).toBe(AGENT);
    expect(h.steer.mock.calls[0][1]).toBe('also send the summary');
    expect(h.run).not.toHaveBeenCalled();
    const b = bubbles().find((m) => m.content === 'also send the summary');
    expect(b?.steerStatus).toBe('queued');
  });

  it('steer() returning false → bubble is marked rejected (not left queued), run() still not called', () => {
    h.isLoading = true;
    h.steer.mockReturnValue(false);
    seedStreaming(true);
    render(<ChatPanel />, { wrapper: MemoryRouter });
    typeInComposer('too late');
    act(() => {
      fireEvent.click(screen.getByTitle('Send into this run (Enter)'));
    });
    expect(h.steer).toHaveBeenCalledTimes(1);
    expect(h.run).not.toHaveBeenCalled();
    const b = bubbles().find((m) => m.content === 'too late');
    expect(b?.steerStatus).toBe('rejected');
    expect(b?.rejectReason).toBe('not_sent');
  });

  it('non-steerable run → no steer send button, and Enter neither steers nor starts a run', () => {
    h.isLoading = true;
    seedStreaming(false);
    render(<ChatPanel />, { wrapper: MemoryRouter });
    const box = typeInComposer('hold this');
    // No steer button on a non-steerable run.
    expect(screen.queryByTitle('Send into this run (Enter)')).toBeNull();
    act(() => {
      fireEvent.keyDown(box, { key: 'Enter' });
    });
    expect(h.steer).not.toHaveBeenCalled();
    expect(h.run).not.toHaveBeenCalled();
    // The draft is held, not turned into a bubble.
    expect(bubbles().some((m) => m.content === 'hold this')).toBe(false);
  });
});
