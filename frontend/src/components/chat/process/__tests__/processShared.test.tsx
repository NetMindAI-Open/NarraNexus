/**
 * processShared — the phase whitelist and the settled-phase rule.
 *
 * The terminal-style process rows this file used to export retired on
 * 2026-08-30: the process renders in the message flow through TurnTimeline
 * now, one shape for live, settled and observed turns alike.
 */
import { describe, it, expect } from 'vitest';
import { PHASE_STEP_IDS, phaseSettled } from '../processShared';
import type { Step } from '@/types';

function step(id: string, status: Step['status'] = 'running'): Step {
  return { id: `s-${id}`, step: id, title: `Step ${id}`, description: '', status, substeps: [], timestamp: 0 };
}

describe('PHASE_STEP_IDS (derived whitelist)', () => {
  it('is derived from PHASE_LABEL_KEYS and holds the top-level phases only', () => {
    for (const id of ['0', '1', '2', '2.5', '3', '3.4']) {
      expect(PHASE_STEP_IDS.has(id)).toBe(true);
    }
    // Sub-steps / echo / housekeeping are NOT phases.
    for (const id of ['3.4.1', '3.4.replay', '3.5', '4', '5']) {
      expect(PHASE_STEP_IDS.has(id)).toBe(false);
    }
  });
});

describe('phaseSettled', () => {
  // The shared settled rule MUST be fed the UNFILTERED steps: "a later phase
  // started" is how the run-agent (3.4) row settles via housekeeping ids
  // (4/5) that the phase whitelist drops. Passing the filtered list silently
  // breaks it — this is the bug the extraction (Minor 4) exists to prevent.
  it('settles a running phase when a later phase id is present in the full step list', () => {
    const full = [step('3.4', 'running'), step('4', 'running')];
    expect(phaseSettled(step('3.4', 'running'), full, false)).toBe(true);
  });

  it('does NOT settle when the caller passes only the whitelisted (filtered) steps', () => {
    // '4' filtered out → no later phase visible → 3.4 must stay running.
    const filtered = [step('3.4', 'running')];
    expect(phaseSettled(step('3.4', 'running'), filtered, false)).toBe(false);
  });

  it('own completed status settles regardless of what else is present', () => {
    expect(phaseSettled(step('3.4', 'completed'), [step('3.4', 'completed')], false)).toBe(true);
  });

  it('a pre-loop phase (step < 3.4) settles once process events have arrived', () => {
    expect(phaseSettled(step('3', 'running'), [step('3', 'running')], true)).toBe(true);
    // ...but the run-agent phase (3.4) is NOT settled by process events alone.
    expect(phaseSettled(step('3.4', 'running'), [step('3.4', 'running')], true)).toBe(false);
  });
});
