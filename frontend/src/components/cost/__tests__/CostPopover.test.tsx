/**
 * @file_name: CostPopover.test.tsx
 * @date: 2026-07-15
 * @description: Regression tests for provider-neutral token usage labels.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CostPopover } from '../CostPopover';
import type { CostSummary } from '@/types/api';

// Cache-heavy shape on purpose: the live regression was an agent whose
// input side was >99% cache tokens, which the popover used to drop entirely
// (showing "input 213" for a 1.2M-token week and making the helper row look
// bigger than the main loop).
const cacheHeavySummary: CostSummary = {
  total_cost_usd: 0,
  total_input_tokens: 100,
  total_output_tokens: 50,
  total_cache_read_tokens: 820,
  total_cache_creation_tokens: 110,
  by_model: {
    '__main_model__': {
      cost: 0,
      input_tokens: 80,
      output_tokens: 40,
      cache_read_tokens: 800,
      cache_creation_tokens: 100,
      call_count: 1,
    },
    '__helper_model__': {
      cost: 0,
      input_tokens: 20,
      output_tokens: 10,
      cache_read_tokens: 20,
      cache_creation_tokens: 10,
      call_count: 1,
    },
  },
  daily: [],
};

const mockState = { costSummary: cacheHeavySummary };

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_test' }),
  usePreloadStore: () => ({
    costSummary: mockState.costSummary,
    costLoading: false,
    refreshCost: vi.fn(),
  }),
  useChatStore: (
    selector: (state: { isStreaming: boolean }) => unknown,
  ) => selector({ isStreaming: false }),
}));

vi.mock('@/lib/api', () => ({
  api: {
    getCosts: vi.fn(),
  },
}));

describe('CostPopover', () => {
  beforeEach(() => {
    mockState.costSummary = cacheHeavySummary;
  });

  it('shows provider-neutral labels for main and helper usage', () => {
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    expect(screen.getByText('Model usage')).toBeInTheDocument();
    expect(screen.getByText('Helper Model Usage')).toBeInTheDocument();
    expect(screen.queryByText('Claude Code')).not.toBeInTheDocument();
    expect(screen.queryByText('__main_model__')).not.toBeInTheDocument();
    expect(screen.queryByText('__helper_model__')).not.toBeInTheDocument();
  });

  it('counts cache buckets into every displayed total', () => {
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    // Grand total = 100 in + 820 cache read + 110 cache write + 50 out.
    expect(screen.getByText('1.1k')).toBeInTheDocument();
    // in/out line: input side includes both cache buckets (1030 → "1.0k").
    expect(screen.getByText('1.0k in / 50 out')).toBeInTheDocument();
    // The subline shows the hit rate (820/1030 → 80%); the raw read/write
    // counts live in its tooltip.
    const subline = screen.getByText('80% cache hit');
    expect(subline).toHaveAttribute('title', 'cache read 820 · write 110');
    // Per-model rows: main 80+40+800+100=1020 ("1.0k"), helper 20+10+20+10=60.
    // Without the cache buckets the helper row (30) would outrank main (120)
    // in the old sort — the live regression this guards against.
    expect(screen.getByText('60')).toBeInTheDocument();
    const rows = screen.getAllByText(/^1\.0k$/);
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it('renders numbers, not NaN, when a summary lacks the cache fields', () => {
    // A backend build predating the cache fields (or a response cached by
    // one) sends no cache keys at all. undefined in a sum renders "NaNM" —
    // the live regression right after deploying the cache-aware frontend
    // against a not-yet-restarted backend.
    mockState.costSummary = {
      total_cost_usd: 0,
      total_input_tokens: 100,
      total_output_tokens: 50,
      by_model: {
        '__main_model__': {
          cost: 0,
          input_tokens: 80,
          output_tokens: 40,
          call_count: 1,
        },
      },
      daily: [{ date: '2026-07-30', input_tokens: 100, output_tokens: 50 }],
    };
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    // Totals fall back to input+output alone, and the cache line is hidden.
    expect(screen.getAllByText('150').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/cache hit/)).not.toBeInTheDocument();
  });

  it('keeps cost off the face; token figures carry it as hover tooltips', () => {
    mockState.costSummary = {
      ...cacheHeavySummary,
      total_cost_usd: 2.39,
      by_model: {
        '__main_model__': {
          ...cacheHeavySummary.by_model['__main_model__'],
          cost: 2.2,
        },
        '__helper_model__': {
          ...cacheHeavySummary.by_model['__helper_model__'],
          cost: 0.19,
        },
      },
    };
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    // No visible dollar amounts anywhere on the panel.
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
    // Grand total tooltip carries the total cost…
    expect(screen.getByText('1.1k')).toHaveAttribute('title', '$2.39 total');
    // …and each model row's token figure carries its own.
    expect(screen.getByText('1.0k')).toHaveAttribute('title', '$2.20');
    expect(screen.getByText('60')).toHaveAttribute('title', '$0.19');
  });

  it('renders no cost tooltip when the model is unpriced ($0)', () => {
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    // Default mock books $0 everywhere: a "$0.00" tooltip would read as
    // "free" rather than "unknown", so there must be none at all.
    expect(screen.getByText('1.1k')).not.toHaveAttribute('title');
    expect(screen.getByText('60')).not.toHaveAttribute('title');
  });
  it('never renders a real cost as $0.0000', () => {
    // toFixed(4) bottoms out below a hundredth of a cent, and an
    // embedding-heavy day lands there. A displayed zero reads as "free" — the
    // exact confusion the > 0 gate above exists to avoid — so a genuinely
    // non-zero amount must still say so (2026-08-03 review).
    mockState.costSummary = {
      ...cacheHeavySummary,
      total_cost_usd: 0.00003,
      by_model: {
        ...cacheHeavySummary.by_model,
        '__helper_model__': {
          ...cacheHeavySummary.by_model['__helper_model__'],
          cost: 0.00003,
        },
      },
    };
    render(<CostPopover />);

    fireEvent.click(screen.getByTitle('Token usage — click for details'));

    expect(screen.getByText('1.1k')).toHaveAttribute('title', '<$0.0001 total');
    expect(screen.getByText('60')).toHaveAttribute('title', '<$0.0001');
  });
});
