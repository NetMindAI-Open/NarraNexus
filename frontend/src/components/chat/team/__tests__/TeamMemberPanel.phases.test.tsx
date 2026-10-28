/**
 * @file_name: TeamMemberPanel.phases.test.tsx
 * @description: The team member panel settles the run-agent (3.4) phase row
 * from the SAME shared rule RunPhases uses — fed the UNFILTERED
 * observation.steps, not the whitelisted `phases`.
 *
 * Why this test exists: the panel renders only the whitelisted phases
 * (PHASE_STEP_IDS), but "a later phase started" is how the run-agent row
 * settles via a housekeeping id (4/5) that the whitelist drops. If the caller
 * passed the filtered `phases` to phaseSettled, `phases.some(> 3.4)` is always
 * false and the 3.4 row spins forever whenever the loop-end COMPLETED is
 * missed (error path / early return). This pins that it passes the full list —
 * symmetric to runPhases.test's phase-row coverage.
 */
import { describe, expect, test, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

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

/** The run-agent phase row (label = the i18n key under the t:(k)=>k mock). */
function runAgentRow(): HTMLElement {
  return screen.getByText('chat.execution.runningAgent').parentElement as HTMLElement;
}

beforeEach(() => {
  observation.status = 'live';
  observation.errorMessage = null;
  observation.circuitReason = null;
  observation.events = [];
  observation.steps = [];
});

describe('TeamMemberPanel — run-agent phase settling', () => {
  test('3.4 settles (✓) once a later housekeeping phase (4) appears in the full steps', () => {
    // '4' is NOT a whitelisted phase, so it never renders as a row — but the
    // shared rule must still see it (via the unfiltered steps) to settle 3.4.
    observation.steps = [
      { step: '3.4', status: 'running', title: 'Run Agent' },
      { step: '4', status: 'running', title: 'Persist Results' },
    ];
    render(<TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={Date.now()} open />);
    expect(runAgentRow().textContent).toContain('✓');
  });

  test('3.4 stays running (spinner, no ✓) while it is the latest step', () => {
    observation.steps = [{ step: '3.4', status: 'running', title: 'Run Agent' }];
    render(<TeamMemberPanel activity={ACTIVITY as never} name="Ana" now={Date.now()} open />);
    const row = runAgentRow();
    expect(row.textContent).not.toContain('✓');
    expect(row.querySelector('svg')).toBeTruthy(); // the spinner
  });
});
