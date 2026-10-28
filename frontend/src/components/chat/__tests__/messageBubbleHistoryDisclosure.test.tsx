/**
 * A historical turn's process must land in the SAME document shape as a live
 * one — and getting there must not be a one-way door.
 *
 * The bug this pins (reported 2026-08-31): clicking "view reasoning" fetched
 * the event log, which made `segmentsForRender` non-null, which unmounted the
 * very block the button lived in. The button vanished mid-interaction with no
 * way back.
 *
 * What opening it must yield is the turn's CONCLUSIONS — the narration and
 * the tool lines — with the provider's raw chain-of-thought still folded
 * behind its own toggle. Owner's call, 2026-08-31: opening the disclosure is
 * not a request to read the model's scratch paper.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const getEventLog = vi.fn();
vi.mock('@/lib/api', () => ({ api: { getEventLog: (...a: unknown[]) => getEventLog(...a) } }));

import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

const message: ChatMessage = {
  id: 'm1', role: 'assistant', content: 'The config is enabled.', timestamp: 0,
};

/** A turn that ends in a reply tool — the shape segmentTurn can cut. */
const withReply = {
  success: true, thinking: '', tool_calls: [],
  timeline: [
    { type: 'thinking', content: 'Checking the flag first.', monologue: true },
    { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'grep flag' } },
    { type: 'thinking', content: 'Weighing which branch reads it.' },
    { type: 'tool_call', tool_name: 'reply_owner', tool_input: { content: 'The config is enabled.' } },
  ],
};

/** A background turn: real work, no user-facing reply. segmentTurn cuts nothing. */
const withoutReply = {
  success: true, thinking: '', tool_calls: [],
  timeline: [
    { type: 'thinking', content: 'Checking the flag first.', monologue: true },
    { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'grep flag' } },
    { type: 'thinking', content: 'Weighing which branch reads it.' },
  ],
};

function mount() {
  return render(
    <MemoryRouter>
      <MessageBubble message={message} eventId="ev1" agentId="a1" />
    </MemoryRouter>,
  );
}

describe('historical turn disclosure', () => {
  beforeEach(() => getEventLog.mockReset());

  it('the affordance survives the fetch that it triggered', async () => {
    // The trap: the block holding the button was gated on segmentsForRender
    // being null, and the fetch is exactly what makes it non-null.
    getEventLog.mockResolvedValue(withReply);
    mount();
    fireEvent.click(screen.getByRole('button', { name: /reasoning|process/i }));
    await waitFor(() => expect(screen.getByText('Checking the flag first.')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: /reasoning|process/i })).toBeInTheDocument();
  });

  it('what it opened, it can close', async () => {
    getEventLog.mockResolvedValue(withReply);
    mount();
    const toggle = () => screen.getByRole('button', { name: /reasoning|process/i });
    fireEvent.click(toggle());
    await waitFor(() => expect(screen.getByText('Checking the flag first.')).toBeInTheDocument());

    fireEvent.click(toggle());
    await waitFor(() => expect(screen.queryByText('Checking the flag first.')).toBeNull());
    // The reply itself is never hidden — only the process folds away.
    expect(screen.getByText(/The config is enabled\./)).toBeInTheDocument();
  });

  it('opens to the conclusions, not to the raw chain-of-thought', async () => {
    // The narration ("what I am about to do") and the tool lines are the
    // turn's readable spine. The provider's scratch paper stays folded — it
    // is bulkier than everything else combined and is not why anyone opened
    // this. It stays one click away, never discarded (iron rule #16).
    getEventLog.mockResolvedValue(withReply);
    mount();
    fireEvent.click(screen.getByRole('button', { name: /reasoning|process/i }));

    await waitFor(() => expect(screen.getByText('Checking the flag first.')).toBeInTheDocument());
    expect(screen.getByText('bash')).toBeInTheDocument();
    expect(screen.queryByText('Weighing which branch reads it.')).toBeNull();
    expect(screen.getAllByRole('button', { expanded: false }).length).toBeGreaterThan(0);
  });

  it('the folded reasoning is still reachable by hand', async () => {
    getEventLog.mockResolvedValue(withReply);
    mount();
    fireEvent.click(screen.getByRole('button', { name: /reasoning|process/i }));
    await waitFor(() => expect(screen.getByText('Checking the flag first.')).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole('button', { expanded: false })[0]);
    expect(screen.getByText('Weighing which branch reads it.')).toBeInTheDocument();
  });

  it('a turn with no reply behaves identically', async () => {
    // segmentTurn cuts no segment here, so this branch renders through a
    // different path — which is precisely why it drifted out of step.
    getEventLog.mockResolvedValue(withoutReply);
    mount();
    fireEvent.click(screen.getByRole('button', { name: /reasoning|process/i }));

    await waitFor(() => expect(screen.getByText('Checking the flag first.')).toBeInTheDocument());
    expect(screen.queryByText('Weighing which branch reads it.')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /reasoning|process/i }));
    await waitFor(() => expect(screen.queryByText('Checking the flag first.')).toBeNull());
  });
});
