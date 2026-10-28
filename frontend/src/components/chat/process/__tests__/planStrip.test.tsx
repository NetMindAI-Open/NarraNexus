/**
 * PlanStrip — the agent's live plan, pinned above the composer.
 *
 * Pinning is the reason it did not move into the document with everything
 * else: "where are we now" must not scroll away. But pinned does not mean
 * framed — the strip sits on the page ground with a hairline rule, not in
 * the terminal box it used to live in.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlanStrip } from '../PlanStrip';
import type { TurnEvent } from '@/types';

const plan: TurnEvent = {
  id: 'p1', ts: 5, type: 'plan', steps: [
    { step: 'Scaffold the directory', status: 'completed' },
    { step: 'Write the body', status: 'in_progress' },
    { step: 'Register it', status: 'pending' },
  ],
};

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: 'Reading the requirement' },
  { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'glob', tool_input: {} },
  plan,
];

describe('PlanStrip', () => {
  it('takes no surface: hairline rule only, no box', () => {
    render(<PlanStrip events={events} />);
    const strip = screen.getByTestId('process-plan');
    expect(strip.className).not.toMatch(/\bshadow\b|rounded-\[var\(--radius/);
    expect(strip.className).not.toMatch(/nm-paper/);
    // A single top hairline is the permitted separator (design_system §2.6).
    expect(strip.className).toMatch(/border-t/);
  });

  it('renders every step of the plan', () => {
    render(<PlanStrip events={events} />);
    const strip = screen.getByTestId('process-plan');
    expect(strip).toHaveTextContent('Scaffold the directory');
    expect(strip).toHaveTextContent('Write the body');
    expect(strip).toHaveTextContent('Register it');
  });

  it('renders only the latest snapshot when the plan is replaced', () => {
    // Plans are replace-on-write: each update is the whole plan, so an
    // append-style render would stack every revision on screen.
    render(
      <PlanStrip
        events={[
          ...events,
          { id: 'p2', ts: 6, type: 'plan', steps: [{ step: 'Wrap up', status: 'in_progress' }] },
        ]}
      />,
    );
    const strip = screen.getByTestId('process-plan');
    expect(strip).toHaveTextContent('Wrap up');
    expect(strip).not.toHaveTextContent('Scaffold the directory');
  });

  it('shows completed-over-total progress', () => {
    render(<PlanStrip events={events} />);
    expect(screen.getByTestId('process-plan')).toHaveTextContent('1/3');
  });

  it('renders nothing at all when there is no plan', () => {
    // Most turns have no plan; an empty strip would be a permanent
    // unexplained rule above the composer.
    const { container } = render(
      <PlanStrip events={events.filter((e) => e.type !== 'plan')} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('never draws the turn content', () => {
    render(<PlanStrip events={events} />);
    expect(screen.queryByText(/Reading the requirement/)).toBeNull();
    expect(screen.queryByTestId('tool-row-c1')).toBeNull();
  });
});
