/**
 * Division of labour: TurnTimeline renders the PROCESS only.
 *
 * The answer tier belongs to the bubble (SegmentedReply); if the timeline also
 * rendered reply / native_output, the same sentence would appear twice — once
 * in the bubble and once in the process region.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TurnTimeline } from '../TurnTimeline';
import type { TurnEvent } from '@/types';

describe('TurnTimeline renders process only', () => {
  it('does not render reply or native_output', () => {
    const events: TurnEvent[] = [
      { id: 't1', ts: 1, type: 'thinking', content: 'reasoning body' },
      { id: 'r1', ts: 2, type: 'reply', content: 'this reply must not appear' },
      { id: 'n1', ts: 3, type: 'native_output', content: 'this native output must not appear' },
    ];
    render(<TurnTimeline events={events} />);
    // Reasoning collapses by default since 2026-08-30, so its body shows only
    // after expanding — while reply / native_output are not rendered at all.
    // Telling those two cases apart is what this case guards.
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(screen.getByText(/reasoning body/)).toBeInTheDocument();
    expect(screen.queryByText(/this reply must not appear/)).toBeNull();
    expect(screen.queryByText(/this native output must not appear/)).toBeNull();
  });

  it('renders nothing at all when the turn holds answer-tier events only', () => {
    const { container } = render(
      <TurnTimeline events={[{ id: 'r1', ts: 1, type: 'reply', content: 'answer only' }]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('does not render the plan either - that belongs to the pinned PlanStrip', () => {
    render(
      <TurnTimeline
        events={[
          { id: 't1', ts: 1, type: 'thinking', content: 'reasoning body' },
          { id: 'p1', ts: 2, type: 'plan', steps: [{ step: 'some step', status: 'pending' }] },
        ]}
      />,
    );
    expect(screen.queryByText('some step')).toBeNull();
  });
});
