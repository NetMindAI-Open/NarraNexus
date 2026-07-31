/**
 * @file_name: useRunObservation.test.ts
 * @date: 2026-07-31
 * @description: The observation frame reducer — one run's WS frames fold
 * into a renderable snapshot. Locks the frame dialect shared with the
 * chat reconnect path (replay kinds, live frames, terminal frames).
 */
import { describe, expect, it } from 'vitest';
import {
  applyObservationFrame,
  type RunObservationSnapshot,
} from '../useRunObservation';

const INITIAL: RunObservationSnapshot = {
  status: 'connecting',
  endState: null,
  events: [],
  steps: [],
  startedAt: null,
  errorMessage: null,
  opsCount: 0,
};

function feed(frames: Array<Record<string, unknown>>): RunObservationSnapshot {
  return frames.reduce(applyObservationFrame, INITIAL);
}

describe('applyObservationFrame', () => {
  it('run_reconnect flips to live and anchors startedAt', () => {
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running', started_at: '2026-07-31T10:00:00Z' },
    ]);
    expect(snap.status).toBe('live');
    expect(snap.startedAt).toBe(Date.parse('2026-07-31T10:00:00Z'));
  });

  it('replay rows materialise the same blocks the live path builds', () => {
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'replay', kind: 'thinking_segment', seq: 1, payload: 'pondering' },
      {
        type: 'replay', kind: 'tool_call', seq: 2,
        payload: JSON.stringify({ tool_name: 'Bash', arguments: { command: 'ls' }, step: '3.4.1' }),
      },
      {
        type: 'replay', kind: 'tool_output', seq: 3,
        payload: JSON.stringify({ output: 'file.txt', step: '3.4.1' }),
      },
    ]);
    expect(snap.events.map((e) => e.type)).toEqual(['thinking', 'tool_call', 'tool_output']);
    const call = snap.events[1];
    expect(call.type === 'tool_call' && call.tool_name).toBe('Bash');
  });

  it('consecutive thinking chunks merge into one block', () => {
    const snap = feed([
      { type: 'agent_thinking', thinking_content: 'part1 ' },
      { type: 'agent_thinking', thinking_content: 'part2' },
    ]);
    expect(snap.events).toHaveLength(1);
    expect(snap.events[0].type === 'thinking' && snap.events[0].content).toBe('part1 part2');
  });

  it('pending tool call is replaced in place by the completed form', () => {
    const snap = feed([
      {
        type: 'progress', step: '3.4.1', title: '🔧 Bash', status: 'running',
        details: { tool_name: 'Bash', arguments: {}, tool_call_id: 'c1', pending: true },
      },
      {
        type: 'progress', step: '3.4.1', title: '🔧 Bash', status: 'running',
        details: { tool_name: 'Bash', arguments: { command: 'ls' }, tool_call_id: 'c1' },
      },
    ]);
    const calls = snap.events.filter((e) => e.type === 'tool_call');
    expect(calls).toHaveLength(1);
    expect(calls[0].type === 'tool_call' && calls[0].pending).toBe(false);
    expect(calls[0].type === 'tool_call' && calls[0].tool_input).toEqual({ command: 'ls' });
  });

  it('pipeline phases upsert by step id, not append', () => {
    const snap = feed([
      { type: 'progress', step: '1', title: 'Loading context', status: 'running', details: {} },
      { type: 'progress', step: '1', title: 'Loading context', status: 'completed', details: {} },
      { type: 'progress', step: '3', title: 'Building context', status: 'running', details: {} },
    ]);
    expect(snap.steps.map((s) => [s.step, s.status])).toEqual([
      ['1', 'completed'],
      ['3', 'running'],
    ]);
    expect(snap.events).toHaveLength(0); // phases are steps, not blocks
  });

  it('plan snapshots replace in place', () => {
    const snap = feed([
      { type: 'agent_plan', steps: [{ step: 'a', status: 'in_progress' }] },
      { type: 'agent_plan', steps: [{ step: 'a', status: 'completed' }, { step: 'b', status: 'in_progress' }] },
    ]);
    const plans = snap.events.filter((e) => e.type === 'plan');
    expect(plans).toHaveLength(1);
    expect(plans[0].type === 'plan' && plans[0].steps).toHaveLength(2);
  });

  it('a reconnect replay does not stack the trace twice', () => {
    // The observe endpoint replays from seq 0 on every attach; the
    // run_reconnect frame that leads each replay must reset the
    // snapshot, otherwise a mid-run socket drop + backoff reopen
    // doubles every block.
    const replay: Array<Record<string, unknown>> = [
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running', started_at: '2026-07-31T10:00:00Z' },
      { type: 'replay', kind: 'thinking_segment', seq: 1, payload: 'pondering' },
      {
        type: 'replay', kind: 'tool_output', seq: 2,
        payload: JSON.stringify({ output: 'file.txt', step: '3.4.1' }),
      },
    ];
    const snap = feed([...replay, ...replay]); // drop + reopen → full replay again
    expect(snap.events.map((e) => e.type)).toEqual(['thinking', 'tool_output']);
    expect(snap.events[0].type === 'thinking' && snap.events[0].content).toBe('pondering');
  });

  it('run_ended is terminal and carries the state + error', () => {
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'run_ended', state: 'failed', final_output: '', error_message: 'Run lost' },
    ]);
    expect(snap.status).toBe('ended');
    expect(snap.endState).toBe('failed');
    expect(snap.errorMessage).toBe('Run lost');
  });

  it('live complete frame ends the observation too', () => {
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'complete', state: 'completed' },
    ]);
    expect(snap.status).toBe('ended');
    expect(snap.endState).toBe('completed');
  });

  it('fatal errors surface; recoverable ones stay quiet', () => {
    const fatal = feed([
      { type: 'error', severity: 'fatal', error_message: 'dead key', error_type: 'auth' },
    ]);
    expect(fatal.errorMessage).toBe('dead key');
    const recovered = feed([
      { type: 'error', severity: 'recovered', error_message: 'blip', error_type: 'api' },
    ]);
    expect(recovered.errorMessage).toBeNull();
  });

  it('agent_response deltas are not the observer surface', () => {
    const snap = feed([
      { type: 'agent_response', response_type: 'text', delta: 'hello' },
    ]);
    expect(snap.events).toHaveLength(0);
  });
});
