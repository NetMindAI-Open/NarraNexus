/**
 * handleSubmit awaits the studio's encode before the composer clears and the
 * run starts. A second Enter in that window used to run the whole path again
 * and send the same message twice — this pins the one-submit-at-a-time gate.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const h = vi.hoisted(() => {
  let release: (v: string) => void = () => undefined;
  return {
    run: vi.fn(),
    release: (v: string) => release(v),
    encodeOutgoing: vi.fn(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        }),
    ),
  };
});

vi.mock('@/hooks/useStudioTurn', () => ({
  useStudioTurn: () => ({ encodeOutgoing: h.encodeOutgoing, applyFromReply: vi.fn() }),
}));

vi.mock('@/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAgentWebSocket: () => ({ run: h.run, reconnect: vi.fn(), stop: vi.fn(), steer: vi.fn(() => true), isLoading: false }),
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

describe('ChatPanel handleSubmit — one submit at a time', () => {
  beforeEach(() => {
    h.run.mockClear();
    h.encodeOutgoing.mockClear();
    useChatStore.setState({ agentSessions: {}, activeAgentId: AGENT });
    useConfigStore.setState({ agentId: AGENT, userId: 'u1', agents: [{ agent_id: AGENT, name: 'A' } as never] });
  });

  it('a second Enter while the first send is still encoding does not send twice', async () => {
    render(<ChatPanel />, { wrapper: MemoryRouter });
    const box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'hello' } });
    act(() => {
      fireEvent.keyDown(box, { key: 'Enter' });
      fireEvent.keyDown(box, { key: 'Enter' });
    });
    expect(h.encodeOutgoing).toHaveBeenCalledTimes(1);
    await act(async () => {
      h.release('hello');
    });
    expect(h.run).toHaveBeenCalledTimes(1);
    const userMessages = (useChatStore.getState().agentSessions[AGENT]?.messages ?? []).filter(
      (m) => m.role === 'user',
    );
    expect(userMessages).toHaveLength(1);
  });
});
