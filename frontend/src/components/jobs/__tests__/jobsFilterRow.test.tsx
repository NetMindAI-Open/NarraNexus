/**
 * @file_name: jobsFilterRow.test.tsx
 * @date: 2026-07-30
 * @description: Regression guard for the job status filter row.
 *
 * The row used to be a single `whitespace-nowrap` line inside a
 * `ScrollArea horizontal hideScrollbar`. The 11 status chips come to roughly
 * 660px in zh, while the panel's usual home is a 300–440px bookmark drawer —
 * so the row cut off after ~7 chips, showed no scrollbar, and gave no hint
 * that more filters existed. They were rendered but unreachable in practice.
 *
 * These tests assert the two properties that fix has to keep:
 *   1. every status chip is rendered, and
 *   2. the row WRAPS instead of relying on invisible horizontal scrolling.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { JobsPanel } from '../JobsPanel';

/** The filter chips, in render order — mirrors the array in JobsPanel. */
const CHIP_LABELS = [
  'All',
  'Active',
  'Running',
  'Paused',
  'No quota',
  'Retrying',
  'Dep failed',
  'Pending',
  'Completed',
  'Failed',
  'Cancelled',
];

describe('JobsPanel — status filter row', () => {
  it('renders every status chip', () => {
    render(<JobsPanel embedded />);
    for (const label of CHIP_LABELS) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('wraps the chips instead of hiding them behind a hidden scrollbar', () => {
    render(<JobsPanel embedded />);
    const row = screen.getByRole('button', { name: 'All' }).parentElement as HTMLElement;

    expect(row.className).toContain('flex-wrap');

    // And it is no longer inside a horizontal scroll viewport: that viewport
    // (with `hideScrollbar`) is what clipped four chips away with no visible
    // affordance to scroll to them.
    expect(row.closest('[data-radix-scroll-area-viewport]')).toBeNull();
  });
});
