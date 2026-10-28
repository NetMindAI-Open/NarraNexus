/**
 * Per-turn token usage in the Conversation view.
 *
 * The event-log response has carried `meta` (per-event token sums, cost,
 * models, duration) since the run-card upgrade, but only the Inner Thoughts
 * card rendered it — a user reading their own chat could see the agent-level
 * 7-day total in the header popover and nothing per turn. The bubble already
 * fetches the same response for its reasoning disclosure, so the chips cost
 * no extra request.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const getEventLogMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: { getEventLog: (...args: unknown[]) => getEventLogMock(...args) },
}));

import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

const message: ChatMessage = {
  id: 'm1',
  role: 'assistant',
  content: 'done',
  timestamp: 0,
};

const baseMeta = {
  trigger: 'chat',
  trigger_source: 'chat',
  input_text: 'what did we ship this week',
  final_output: 'done',
  state: 'completed',
  started_at: '2026-08-28 08:00:00',
  finished_at: '2026-08-28 08:01:30',
  duration_seconds: 90,
  models: ['claude-opus-5'],
  total_cost_usd: 0.0041,
  input_tokens: 1250,
  output_tokens: 300,
  tool_call_count: 0,
};

function mockResponse(meta: Record<string, unknown>) {
  getEventLogMock.mockResolvedValue({
    success: true,
    event_id: 'ev1',
    thinking: 'planning',
    tool_calls: [],
    timeline: [{ type: 'thinking', content: 'planning' }],
    meta,
  });
}

function expand() {
  render(
    <MemoryRouter>
      <MessageBubble message={message} eventId="ev1" agentId="a1" />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: /reasoning/i }));
}

describe('MessageBubble per-turn run stats', () => {
  beforeEach(() => getEventLogMock.mockReset());

  it('shows this turn’s tokens, cost, duration and model', async () => {
    mockResponse(baseMeta);
    expand();

    await waitFor(() => expect(screen.getByTestId('run-stat-chips')).toBeTruthy());
    expect(screen.getByText(/1\.3k.*300/)).toBeTruthy();
    expect(screen.getByText('$0.0041')).toBeTruthy();
    expect(screen.getByText('1m 30s')).toBeTruthy();
    expect(screen.getByText('claude-opus-5')).toBeTruthy();
  });

  it('counts the cache buckets into the input side', async () => {
    // A cache-warm turn: 33 full-rate tokens, 869k actually read. Showing
    // input_tokens alone here would under-report by ~4 orders of magnitude.
    mockResponse({
      ...baseMeta,
      input_tokens: 33,
      output_tokens: 19_528,
      cache_read_tokens: 735_147,
      cache_creation_tokens: 134_071,
    });
    expand();

    await waitFor(() => expect(screen.getByText(/869\.3k.*19\.5k/)).toBeTruthy());
  });

  it('renders no chip row for a legacy turn with no ledger rows', async () => {
    mockResponse({
      ...baseMeta,
      duration_seconds: null,
      models: [],
      total_cost_usd: null,
      input_tokens: 0,
      output_tokens: 0,
    });
    expand();

    await waitFor(() => expect(getEventLogMock).toHaveBeenCalled());
    expect(screen.queryByTestId('run-stat-chips')).toBeNull();
    // And the wrapper is gone too, not just its contents: RunStatChips
    // returning null still leaves the caller's margin-carrying div behind,
    // so the caller has to gate on hasRunStats as well. Without this second
    // assertion, dropping that gate stays green.
    expect(screen.queryByTestId('run-stat-chips-slot')).toBeNull();
  });

  it('hides the cost chip when the ledger booked $0 (unpriced model)', async () => {
    // total_cost_usd === 0 is not a rounding artefact: price_for returns None
    // for any model id the table doesn't know, calculate_cost then books 0,
    // and that is the majority of rows on a local install. Gating on != null
    // let formatCost(0) render "<$0.0001" — "we don't know the rate" shown as
    // "it cost a little something".
    mockResponse({ ...baseMeta, total_cost_usd: 0 });
    expand();

    await waitFor(() => expect(screen.getByTestId('run-stat-chips')).toBeTruthy());
    expect(screen.queryByText(/\$/)).toBeNull();
    // The rest of the row still renders — only the price is unknown.
    expect(screen.getByText(/1\.3k.*300/)).toBeTruthy();
  });

  it('keeps the chips when the turn upgrades to segment mode', async () => {
    // Chips sit OUTSIDE the disclosure: they describe the whole turn, not the
    // process region the disclosure holds.
    // (Historical note: the original reason was that a timeline carrying a
    // reply made segmentTurn produce segments and UNMOUNTED the disclosure,
    // so chips placed inside vanished on exactly the turns that had a reply.
    // That unmount was the bug fixed on 2026-08-31 — the disclosure now
    // survives its own fetch — but outside is still the right place.)
    getEventLogMock.mockResolvedValue({
      success: true,
      event_id: 'ev1',
      thinking: '',
      tool_calls: [],
      timeline: [
        { type: 'thinking', content: 'planning' },
        { type: 'reply', content: 'here is the answer' },
      ],
      meta: baseMeta,
    });
    expand();

    // Segment mode really did take over, and the chips came through it.
    await waitFor(() => expect(screen.getByTestId('segment-reply-0')).toBeTruthy());
    expect(screen.getByTestId('run-stat-chips')).toBeTruthy();
    expect(screen.getByText(/1\.3k.*300/)).toBeTruthy();
  });

  it('survives a backend that returns no meta at all', async () => {
    getEventLogMock.mockResolvedValue({
      success: true,
      event_id: 'ev1',
      thinking: 'planning',
      tool_calls: [],
      timeline: [{ type: 'thinking', content: 'planning' }],
    });
    expand();

    await waitFor(() => expect(getEventLogMock).toHaveBeenCalled());
    expect(screen.queryByTestId('run-stat-chips')).toBeNull();
    // The disclosure itself still renders — meta is additive, not required.
    // Its content is the reasoning toggle rather than the reasoning text:
    // provider chain-of-thought never auto-expands (2026-08-31), so the proof
    // that the fetched turn arrived is the control, one click from the text.
    const reasoning = await screen.findByRole('button', { expanded: false });
    fireEvent.click(reasoning);
    expect(screen.getByText('planning')).toBeTruthy();
  });
});
