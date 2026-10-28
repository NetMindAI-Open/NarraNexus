/**
 * @file_name: executionPopover.test.tsx
 * @date: 2026-06-11
 * @description: Tests for the clickable Processing chip — the execution
 * step list resurrected from the retired RuntimePanel as a popover.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ExecutionPopover } from '../ExecutionPopover';
import type { Step } from '@/types';

function step(id: string, stepNo: string, title: string, status: Step['status']): Step {
  return {
    id, step: stepNo, title, status,
    description: '', substeps: [], timestamp: 0,
  };
}

// Backend titles here are the raw pipeline titles; the popover must show the
// SAME localized phase names RunPhases does (consistency), not the raw
// English backend title.
const STEPS: Step[] = [
  step('s0', '0', 'Initialization', 'completed'),
  step('s1', '1', '📚 Narrative Selection', 'completed'),
  step('s3', '3', 'Build Context', 'completed'),
  step('s34', '3.4', 'Run Agent', 'running'),
];

describe('ExecutionPopover', () => {
  it('shows the current stage on the chip, not a misleading X/Y fraction', () => {
    render(<ExecutionPopover steps={STEPS} />);
    const trigger = screen.getByLabelText('Show execution steps');
    expect(trigger).toHaveTextContent('Processing');
    // The current stage = the latest running step, shown by its LOCALIZED
    // name (3.4 = run agent), matching RunPhases — not the raw title.
    expect(trigger).toHaveTextContent('Running agent');
    // The old "seen-steps-as-total" fraction (2/4, 3/4, …) is gone.
    expect(trigger).not.toHaveTextContent('2/4');
    expect(trigger.textContent).not.toMatch(/\d+\/\d+/);
  });

  // The two surfaces used to disagree: the phase rows showed localized labels
  // while this popover printed the raw backend title for the same step. Now
  // both go through PHASE_LABEL_KEYS; only genuine sub-steps keep raw titles.
  it('main phases show localized labels (consistent with RunPhases); sub-steps keep raw title', () => {
    const steps: Step[] = [
      step('s3', '3', 'Build Context', 'completed'),
      step('s34', '3.4', 'Run Agent', 'running'),
      step('s341', '3.4.1', 'read_file', 'running'),
    ];
    render(<ExecutionPopover steps={steps} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));
    expect(screen.getByText(/Building context/)).toBeInTheDocument();
    expect(screen.getByText(/Running agent/)).toBeInTheDocument();
    // Raw backend title for a mapped phase must NOT leak.
    expect(screen.queryByText('Build Context')).toBeNull();
    expect(screen.queryByText('Run Agent')).toBeNull();
    // A genuine sub-step (not a mapped phase) keeps its own title.
    expect(screen.getByText('read_file')).toBeInTheDocument();
  });

  it('renders each step description and the narrative selection reason', () => {
    const steps: Step[] = [
      {
        id: 's1', step: '1', title: 'Narrative Selection', status: 'completed',
        description: 'Matched narrative "Morning Briefing" (score 0.82)',
        substeps: [],
        details: { selection_reason: 'topic continued from the last turn', selection_method: 'session' },
        timestamp: 0,
      },
    ];
    render(<ExecutionPopover steps={steps} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));
    expect(screen.getByText(/Matched narrative "Morning Briefing"/)).toBeInTheDocument();
    expect(screen.getByText(/topic continued from the last turn/)).toBeInTheDocument();
  });

  it('click opens the step list with statuses', () => {
    render(<ExecutionPopover steps={STEPS} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));

    expect(screen.getByText('Execution')).toBeInTheDocument();
    // Localized phase labels (step 0 / 1 / 3 / 3.4), consistent with RunPhases.
    expect(screen.getByText(/Initializing/)).toBeInTheDocument();
    expect(screen.getByText(/Selecting narrative/)).toBeInTheDocument();
    expect(screen.getByText(/Building context/)).toBeInTheDocument();
    // "Running agent" is the current stage, so it shows on BOTH the chip and
    // the list row — hence getAllByText, not getByText.
    expect(screen.getAllByText(/Running agent/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByLabelText('completed')).toHaveLength(3);
    expect(screen.getAllByLabelText('running')).toHaveLength(1);
  });

  it('empty steps show the waiting placeholder', () => {
    render(<ExecutionPopover steps={[]} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));
    expect(screen.getByText(/Waiting for the first step/i)).toBeInTheDocument();
  });

  it.each([['failed' as const]])('renders %s status icon', (st) => {
    render(<ExecutionPopover steps={[step('sx', '2', 'Boom', st)]} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));
    expect(screen.getByLabelText('failed')).toBeInTheDocument();
  });
});
