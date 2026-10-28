/**
 * @file_name: turnTimelinePlugin.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: A timeline event type registered by a plugin renders inside the turn rail; an unknown type still renders nothing.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { TurnTimeline } from '../TurnTimeline';
import { TIMELINE_EVENTS } from '@/platform/registries';
import { onUiError, resetErrorSink } from '@/platform/errorSink';
import type { TurnEvent } from '@/types';

const disposers: (() => void)[] = [];
afterEach(() => {
  disposers.splice(0).forEach((d) => d());
  resetErrorSink();
});

describe('plugin timeline events', () => {
  it('renders a registered type and ignores an unregistered one', () => {
    disposers.push(TIMELINE_EVENTS.register('acme_tick', { component: ({ event }) => <div data-testid="tick">tick {event.id}</div> }, { owner: 'acme.t' }));
    const events = [
      { id: 'e1', type: 'acme_tick' },
      { id: 'e2', type: 'acme_unknown' },
    ] as unknown as TurnEvent[];
    render(<TurnTimeline events={events} />);
    expect(screen.getByTestId('tick')).toHaveTextContent('tick e1');
    expect(screen.queryByText(/e2/)).toBeNull();
  });

  it('a throwing timeline event component is isolated (I-6): the rest of the rail still renders', () => {
    disposers.push(TIMELINE_EVENTS.register('acme_boom', { component: () => { throw new Error('boom'); } }, { owner: 'acme.broken_plugin' }));
    const seen: unknown[] = [];
    onUiError((r) => seen.push(r));
    const events = [
      { id: 'e0', type: 'thinking', content: 'thinking about it' },
      { id: 'e1', type: 'acme_boom' },
    ] as unknown as TurnEvent[];
    render(<TurnTimeline events={events} />);
    // the "thinking" block (collapsed by default) still rendered — the crashed
    // plugin row was isolated, not the whole rail.
    expect(screen.getByText('Thought')).toBeInTheDocument();
    expect(seen).toEqual([expect.objectContaining({ source: 'acme.broken_plugin' })]);
  });
});
