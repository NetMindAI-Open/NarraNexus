/**
 * Two things about an in-flight run, both reported 2026-08-31.
 *
 * 1. The pipeline preamble must appear as soon as the run starts. The backend
 *    emits step 0/1/2/2.5/3 well before the model produces anything, so a
 *    reader who just hit send should see "selecting narrative" — not a blank
 *    column until the agent loop finally speaks.
 *
 * 2. The run in flight must be rendered ONCE. A 12s history poll picks up the
 *    reply row the backend persists mid-run, and the streaming block is
 *    already rendering that same reply from currentEvents — so the answer
 *    appeared twice, and a refresh "fixed" it because the streaming copy was
 *    then gone.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const h = vi.hoisted(() => ({
  getSimpleChatHistory: vi.fn(),
}));

vi.mock('@/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAgentWebSocket: () => ({
      run: vi.fn(), reconnect: vi.fn(), stop: vi.fn(), steer: vi.fn(), isLoading: true,
    }),
    useFastMode: () => [false, vi.fn()],
  };
});

vi.mock('@/lib/api', () => ({
  api: {
    getSimpleChatHistory: (...a: unknown[]) => h.getSimpleChatHistory(...a),
    getTranscriptionAvailability: vi.fn().mockResolvedValue({ available: false, reason: '' }),
    uploadAttachment: vi.fn(),
  },
}));

import { ChatPanel } from '../ChatPanel';
import { useConfigStore, useChatStore } from '@/stores';

const AGENT = 'a1';
const RUN = 'r1';
const REPLY = 'Just woke up, everything feels new.';

function mount() {
  return render(<ChatPanel />, { wrapper: MemoryRouter });
}

describe('in-flight run visibility', () => {
  beforeEach(() => {
    h.getSimpleChatHistory.mockReset();
    h.getSimpleChatHistory.mockResolvedValue({ success: true, messages: [], total_count: 0 });
    useChatStore.setState({ agentSessions: {}, activeAgentId: AGENT });
    useConfigStore.setState({
      agentId: AGENT,
      userId: 'u1',
      agents: [{ agent_id: AGENT, name: 'Analyst' } as never],
    });
  });

  it('shows the pipeline phases before the agent loop produces anything', () => {
    const cs = useChatStore.getState();
    act(() => {
      cs.startStreaming(AGENT);
      cs.processMessage(AGENT, { type: 'run_started', run_id: RUN, steerable: true });
      // A pre-loop phase: narrative selection. No turn events exist yet — this
      // is precisely the window the preamble is for.
      cs.processMessage(AGENT, {
        type: 'progress', step: '1', title: 'Select Narrative',
        description: '', status: 'running', substeps: [], timestamp: 1,
      });
    });
    mount();

    // Scoped to the preamble: the phase label also exists in the execution
    // popover, so an unscoped query would pass while the reading column stays
    // blank — exactly the bug reported.
    expect(screen.getByTestId('run-phases')).toHaveTextContent(/Selecting narrative/);
  });

  it('says something from the moment the run starts, with no steps yet either', () => {
    const cs = useChatStore.getState();
    act(() => {
      cs.startStreaming(AGENT);
      cs.processMessage(AGENT, { type: 'run_started', run_id: RUN, steerable: true });
    });
    mount();

    expect(screen.getByText(/Starting up/)).toBeInTheDocument();
  });

  it('renders the in-flight reply once, even after the poll persists it', async () => {
    // The persisted row carries the run's event_id — the same stamp the
    // settled-turn dedup already relies on.
    h.getSimpleChatHistory.mockResolvedValue({
      success: true,
      total_count: 1,
      messages: [
        { role: 'assistant', content: REPLY, timestamp: '2026-08-31T00:00:01Z', event_id: RUN },
      ],
    });

    const cs = useChatStore.getState();
    act(() => {
      cs.startStreaming(AGENT);
      cs.processMessage(AGENT, { type: 'run_started', run_id: RUN, steerable: true });
      cs.processMessage(AGENT, {
        type: 'progress', step: '3.4.1', title: 'Tool', description: '',
        status: 'completed', substeps: [], timestamp: 2,
        details: { tool_name: 'reply_owner', arguments: { content: REPLY } },
      });
    });
    mount();

    await waitFor(() => expect(h.getSimpleChatHistory).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByText(REPLY).length).toBe(1));
  });

  it("never swallows the user's own message for the run in flight", async () => {
    // The backend stamps the same event_id on the user row for that turn, and
    // history rows win the timeline dedup — so a filter keyed on event_id
    // alone deletes what the user just typed while the agent works on it.
    h.getSimpleChatHistory.mockResolvedValue({
      success: true,
      total_count: 2,
      messages: [
        { role: 'user', content: 'Hello there', timestamp: '2026-08-31T00:00:00Z', event_id: RUN },
        { role: 'assistant', content: REPLY, timestamp: '2026-08-31T00:00:01Z', event_id: RUN },
      ],
    });

    const cs = useChatStore.getState();
    act(() => {
      cs.startStreaming(AGENT);
      cs.processMessage(AGENT, { type: 'run_started', run_id: RUN, steerable: true });
    });
    mount();

    await waitFor(() => expect(h.getSimpleChatHistory).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByText('Hello there').length).toBe(1));
  });

  it('the persisted row comes back once the run is no longer in flight', async () => {
    // The filter must be scoped to the run in flight — a finished run's row is
    // ordinary history and must not be swallowed (iron rule #16).
    h.getSimpleChatHistory.mockResolvedValue({
      success: true,
      total_count: 1,
      messages: [
        { role: 'assistant', content: REPLY, timestamp: '2026-08-31T00:00:01Z', event_id: RUN },
      ],
    });
    mount();

    await waitFor(() => expect(screen.getAllByText(REPLY).length).toBe(1));
  });
});
