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

const STEPS: Step[] = [
  step('s0', '0', 'Initialize', 'completed'),
  step('s1', '1', 'Load context', 'completed'),
  step('s3', '3', 'Agent loop', 'running'),
  step('s34', '3.4', 'Tool call', 'running'),
];

describe('ExecutionPopover', () => {
  it('shows the Processing chip with progress count', () => {
    render(<ExecutionPopover steps={STEPS} />);
    const trigger = screen.getByLabelText('Show execution steps');
    expect(trigger).toHaveTextContent('Processing');
    expect(trigger).toHaveTextContent('2/4');
  });

  it('click opens the step list with statuses', () => {
    render(<ExecutionPopover steps={STEPS} />);
    fireEvent.click(screen.getByLabelText('Show execution steps'));

    expect(screen.getByText('Execution')).toBeInTheDocument();
    expect(screen.getByText('Initialize')).toBeInTheDocument();
    expect(screen.getByText('Agent loop')).toBeInTheDocument();
    expect(screen.getByText('Tool call')).toBeInTheDocument();
    expect(screen.getAllByLabelText('completed')).toHaveLength(2);
    expect(screen.getAllByLabelText('running')).toHaveLength(2);
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
