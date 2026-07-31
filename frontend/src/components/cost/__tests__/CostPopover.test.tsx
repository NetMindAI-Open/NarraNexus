/**
 * @file_name: CostPopover.test.tsx
 * @date: 2026-07-15
 * @description: Regression tests for provider-neutral token usage labels.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CostPopover } from '../CostPopover';

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_test' }),
  usePreloadStore: () => ({
    costSummary: {
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
        '__helper_model__': {
          cost: 0,
          input_tokens: 20,
          output_tokens: 10,
          call_count: 1,
        },
      },
      daily: [],
    },
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
});
