/**
 * @file_name: TeamMemberPanel.circuit.test.tsx
 * @description: A paused agent says it is paused.
 *
 * When an agent's breaker is open the room showed "Couldn't load the process" —
 * which is not what happened, gives the user nothing to do, and is the same copy
 * a genuine fetch failure produces, so the two were indistinguishable.
 *
 * The honest surface already exists: App.tsx listens for
 * `narranexus:agent-circuit-open` and renders a banner with a one-click Resume
 * that clears the pause. The private chat has fired that event for a long time.
 * This panel is the piece that knows WHICH agent is involved — the observation
 * hook only has a run id — so it is the one that announces.
 *
 * Reusing the event rather than building a second banner is deliberate: a
 * parallel implementation would be a second place to keep in step with the
 * breaker's reason vocabulary, and this codebase has just been through what
 * happens when one rule has two implementations.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const observation = {
  status: 'live' as string,
  endState: null as string | null,
  events: [] as unknown[],
  steps: [] as unknown[],
  startedAt: null as number | null,
  errorMessage: null as string | null,
  circuitReason: null as string | null,
  opsCount: 0,
};

vi.mock('@/hooks/useRunObservation', () => ({
  useRunObservation: () => observation,
  applyObservationFrame: (s: unknown) => s,
}));

vi.mock('@/hooks/useTurnDetail', () => ({
  useTurnDetail: () => ({ kind: 'idle', events: [] }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { TeamMemberPanel } from '../TeamMemberPanel';

const ACTIVITY = {
  agent_id: 'agent_a',
  status: 'running',
  phase: 'thinking',
  event_id: 'evt_1',
  started_at: '2026-08-12T09:00:00Z',
};

function draw() {
  render(
    <TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={Date.now()} open />,
  );
}

describe('a paused agent in a team room', () => {
  let seen: Array<{ agentId: string; reason: string }>;
  const handler = (e: Event) => seen.push((e as CustomEvent).detail);

  beforeEach(() => {
    seen = [];
    window.addEventListener('narranexus:agent-circuit-open', handler);
    observation.status = 'live';
    observation.errorMessage = null;
    observation.circuitReason = null;
    observation.events = [];
  });

  afterEach(() => {
    window.removeEventListener('narranexus:agent-circuit-open', handler);
  });

  test('announces the pause so the existing Resume banner appears', async () => {
    observation.circuitReason = 'paused:quota';
    observation.errorMessage = 'circuit open';

    draw();

    await waitFor(() =>
      expect(seen).toEqual([{ agentId: 'agent_a', reason: 'paused:quota' }]),
    );
  });

  test('says the agent is paused instead of claiming a load failure', async () => {
    observation.circuitReason = 'paused:auth';
    observation.errorMessage = 'circuit open';

    const { container } = render(
      <TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={Date.now()} open />,
    );

    await waitFor(() => {
      expect(container.textContent).not.toContain('detailLoadFailed');
    });
  });

  test('an ordinary observation failure still reads as a load failure', async () => {
    // The honest copy for the honest case: this one really is "we could not read
    // the process". Widening the pause copy to cover it would swap one lie for
    // another.
    observation.circuitReason = null;
    observation.errorMessage = 'not visible to this client';

    const { container } = render(
      <TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={Date.now()} open />,
    );

    await waitFor(() => expect(container.textContent).toContain('detailLoadFailed'));
    expect(seen).toEqual([]);
  });

  test('a healthy run announces nothing', async () => {
    observation.events = [{ type: 'thinking' }];

    draw();

    await new Promise((r) => setTimeout(r, 0));
    expect(seen).toEqual([]);
  });

  test('a changing clock does not re-announce', async () => {
    // The roster ticks a 1s clock, so this runs constantly. What keeps the
    // announcement from repeating is the effect's dependency array, not a ref:
    // an explicit guard was written first, and every mutation of it left the
    // tests green because it was dead code against this case. Removed rather
    // than defended with an assertion for a problem that did not exist.
    observation.circuitReason = 'paused:auth';
    observation.errorMessage = 'circuit open';

    const { rerender } = render(
      <TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={1} open />,
    );
    await waitFor(() => expect(seen).toHaveLength(1));

    for (let i = 2; i < 8; i++) {
      rerender(<TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={i} open />);
    }
    await new Promise((r) => setTimeout(r, 0));

    expect(seen).toHaveLength(1);
  });
});
