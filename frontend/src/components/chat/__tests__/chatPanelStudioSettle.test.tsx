/**
 * The creation studio applies a reply's draft block when a turn SETTLES. The
 * settle detector reads flat store fields that follow whichever agent is
 * active, so this pins the two ways that went wrong:
 *
 *   - switching from a streaming agent A back to an already-settled agent B
 *     must not read as "B just settled" and replay B's old draft over the
 *     edits the user made in the panel since;
 *   - the same settled message is applied at most once, even if the edge is
 *     observed again.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const h = vi.hoisted(() => ({
  applyFromReply: vi.fn<(text: string) => Promise<void>>(async () => undefined),
}));

vi.mock('@/hooks/useStudioTurn', () => ({
  useStudioTurn: () => ({
    encodeOutgoing: async (text: string) => text,
    applyFromReply: h.applyFromReply,
  }),
}));

vi.mock('@/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAgentWebSocket: () => ({
      run: vi.fn(),
      reconnect: vi.fn(),
      stop: vi.fn(),
      steer: vi.fn(() => true),
      isLoading: false,
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

const A = 'agent_a';
const B = 'agent_b';

function switchTo(agentId: string) {
  act(() => {
    useConfigStore.setState({ agentId });
  });
}

describe('ChatPanel — studio settle detection across agents', () => {
  beforeEach(() => {
    h.applyFromReply.mockClear();
    useChatStore.setState({ agentSessions: {}, activeAgentId: B });
    useConfigStore.setState({
      agentId: B,
      userId: 'u1',
      agents: [
        { agent_id: A, name: 'A' } as never,
        { agent_id: B, name: 'B' } as never,
      ],
    });
  });

  it('applies B\'s reply once when B settles, and not again when the view returns to B while A streams', () => {
    render(<ChatPanel />, { wrapper: MemoryRouter });
    const cs = useChatStore.getState();

    // B runs a turn and settles → applied exactly once.
    act(() => cs.startStreaming(B));
    act(() => cs.stopStreaming(B));
    expect(h.applyFromReply).toHaveBeenCalledTimes(1);

    // A starts streaming in the background; the user looks at A…
    act(() => cs.startStreaming(A));
    switchTo(A);
    // …then comes back to B while A is still running. The flat isStreaming
    // goes true → false here, but nothing on B settled.
    switchTo(B);
    expect(h.applyFromReply).toHaveBeenCalledTimes(1);

    // A settling while B is on screen must not apply anything for B either.
    act(() => cs.stopStreaming(A));
    expect(h.applyFromReply).toHaveBeenCalledTimes(1);

    // A genuinely new turn on B is applied.
    act(() => cs.startStreaming(B));
    act(() => cs.stopStreaming(B));
    expect(h.applyFromReply).toHaveBeenCalledTimes(2);
  });
});
