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
  isTerminalErrorFrame,
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

  it('anchors a NAIVE-UTC started_at as UTC — the shape cloud MySQL actually emits', () => {
    // review #349 I1: the 'Z' case above was the only coverage, and the
    // backend never used to send that shape — a bare Date.parse here was
    // permanently green while reading the real frames as LOCAL time.
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running', started_at: '2026-07-31T10:00:00.123456' },
    ]);
    expect(snap.startedAt).toBe(Date.parse('2026-07-31T10:00:00.123456Z'));
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

// ── circuit breaker: the room must say what actually happened ──────────────
//
// An agent whose breaker is open is not "loading". Today that frame falls into
// the generic error branch: it writes an errorMessage, never settles the
// snapshot, and the socket's onclose then reconnects forever with capped
// backoff — against an agent that is, by definition, refusing to run. The room
// shows "Couldn't load the process" while the backend retries in a loop.
//
// The private chat has had the honest path for a long time (wsCircuitOpen.ts →
// a banner with Resume). This is the observation socket learning the same
// thing: a breaker frame is TERMINAL, and it says so.

describe('agent_circuit_open', () => {
  it('settles the run instead of leaving it pending forever', () => {
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      {
        type: 'error',
        error_type: 'agent_circuit_open',
        severity: 'fatal',
        cb_reason: 'paused:quota',
        error_message: 'circuit open',
      },
    ]);

    expect(snap.status).toBe('ended');
    expect(snap.endState).toBe('failed');
  });

  it('keeps the breaker reason, because "failed" alone is not actionable', () => {
    // paused:auth needs a new key, paused:quota needs balance, cooling needs
    // time. Collapsing them into "failed" tells the user to do nothing in
    // particular.
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'error', error_type: 'agent_circuit_open', cb_reason: 'paused:auth' },
    ]);

    expect(snap.circuitReason).toBe('paused:auth');
  });

  it('an ordinary error still leaves the run open', () => {
    // Not every error is terminal — a tool failure mid-run is a run-level event
    // and the run continues. Widening the terminal set would settle runs that
    // are still going.
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'error', error_type: 'ToolError', error_message: 'grep failed' },
    ]);

    expect(snap.status).not.toBe('ended');
  });

  it('a run with no breaker frame reports no reason', () => {
    const snap = feed([{ type: 'run_reconnect', run_id: 'evt_1', state: 'running' }]);
    expect(snap.circuitReason).toBeNull();
  });

  it('the synthetic run_ended the socket injects does not erase the reason', () => {
    // The socket settles a terminal error by dispatching the frame AND a
    // synthesised run_ended. If that second frame reset circuitReason, the
    // banner would know the run failed and not why — which is the whole
    // difference between an actionable message and a red box.
    const snap = feed([
      { type: 'run_reconnect', run_id: 'evt_1', state: 'running' },
      { type: 'error', error_type: 'agent_circuit_open', cb_reason: 'paused:quota' },
      { type: 'run_ended', state: 'failed', error_message: 'circuit open' },
    ]);

    expect(snap.circuitReason).toBe('paused:quota');
    expect(snap.endState).toBe('failed');
  });
});

// ── the retry ladder ───────────────────────────────────────────────────────
//
// `fatalRef` is what stops the socket reconnecting, and it is set from this
// predicate. No reducer test can reach it — the reducer never sees `onclose` —
// which is exactly how the breaker case came to reconnect forever with every
// test green. The acceptance criterion names this behaviour, so it gets its own
// assertions.

describe('isTerminalErrorFrame', () => {
  it('stops retrying on an open breaker', () => {
    // Retrying here is a loop against a refusal: the agent is not down, it is
    // declining to run until someone fixes a key or a balance.
    expect(isTerminalErrorFrame('agent_circuit_open')).toBe(true);
  });

  it('stops retrying on the errors the server closes after', () => {
    for (const et of ['Forbidden', 'NotFound', 'DBError']) {
      expect(isTerminalErrorFrame(et)).toBe(true);
    }
  });

  it('keeps observing through a run-level error', () => {
    // A failed tool call mid-run is not the end of the run. Treating it as
    // terminal would drop the observer while the agent is still working.
    expect(isTerminalErrorFrame('ToolError')).toBe(false);
  });

  it('an absent error_type is not terminal', () => {
    expect(isTerminalErrorFrame(undefined)).toBe(false);
    expect(isTerminalErrorFrame(null)).toBe(false);
  });
});

// ── the Resume path ────────────────────────────────────────────────────────
//
// The app already has the honest surface: `App.tsx` listens for
// `narranexus:agent-circuit-open` and renders a banner with a one-click Resume
// that clears the pause. The private chat has fired that event for a long time;
// the team room's observation socket never did, which is why the room showed a
// generic "couldn't load the process" and offered nothing to do about it.
//
// So the fix is to fire the SAME event, not to build a second banner. A parallel
// implementation would be a second thing to keep in step with the breaker's
// reason vocabulary, and the two would drift the way the palettes did.

describe('the observation socket announces an open breaker', () => {
  it('dispatches the app-wide event the Resume banner already listens for', async () => {
    const { dispatchAgentCircuitOpen } = await import('@/services/wsCircuitOpen');
    const seen: unknown[] = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener('narranexus:agent-circuit-open', handler);

    dispatchAgentCircuitOpen({ agentId: 'agent_a', reason: 'paused:quota' });

    window.removeEventListener('narranexus:agent-circuit-open', handler);
    expect(seen).toEqual([{ agentId: 'agent_a', reason: 'paused:quota' }]);
  });

});

describe('narration tier on the observation path', () => {
  it('a monologue segment becomes a progress-tier block', () => {
    let snap = applyObservationFrame(INITIAL, { type: 'run_reconnect', run_id: 'r1' });
    snap = applyObservationFrame(snap, {
      type: 'replay', kind: 'thinking_segment_monologue', seq: 1, payload: 'Reading the config.',
    });

    const think = snap.events.filter((e) => e.type === 'thinking');
    expect(think.map((e) => [e.content, !!e.monologue])).toEqual([
      ['Reading the config.', true],
    ]);
  });

  it('a tier switch opens a new block instead of gluing two tiers together', () => {
    let snap = applyObservationFrame(INITIAL, { type: 'run_reconnect', run_id: 'r1' });
    snap = applyObservationFrame(snap, {
      type: 'replay', kind: 'thinking_segment', seq: 1, payload: 'weighing options',
    });
    snap = applyObservationFrame(snap, {
      type: 'replay', kind: 'thinking_segment_monologue', seq: 2, payload: 'Reading it now.',
    });

    const think = snap.events.filter((e) => e.type === 'thinking');
    expect(think.map((e) => [e.content, !!e.monologue])).toEqual([
      ['weighing options', false],
      ['Reading it now.', true],
    ]);
  });

  it('same-tier segments still coalesce', () => {
    let snap = applyObservationFrame(INITIAL, { type: 'run_reconnect', run_id: 'r1' });
    snap = applyObservationFrame(snap, {
      type: 'replay', kind: 'thinking_segment_monologue', seq: 1, payload: 'Reading ',
    });
    snap = applyObservationFrame(snap, {
      type: 'replay', kind: 'thinking_segment_monologue', seq: 2, payload: 'the config.',
    });

    const think = snap.events.filter((e) => e.type === 'thinking');
    expect(think.map((e) => [e.content, !!e.monologue])).toEqual([
      ['Reading the config.', true],
    ]);
  });
});
