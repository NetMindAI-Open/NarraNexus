import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { wsManager, translateReconnectFrame } from '../wsManager';
import { useChatStore } from '@/stores/chatStore';
import { PHASE_STEP_IDS } from '@/components/chat/process/processShared';

// ---- Mock WebSocket ----------------------------------------------------
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CLOSED = 3;
  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = MockWebSocket.CLOSED; if (this.onclose) this.onclose(); }
  triggerOpen() { this.readyState = MockWebSocket.OPEN; if (this.onopen) this.onopen(); }
  triggerMessage(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
  triggerClose() { if (this.onclose) this.onclose(); }
}

const AGENT = 'agent_recon';
const USER = 'binliang';

describe('wsManager A3 — auto-reconnect on passive disconnect', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    MockWebSocket.instances = [];
  });

  afterEach(() => {
    wsManager.close(AGENT);
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('schedules a reconnect after a passive disconnect once run_started seen', () => {
    wsManager.run(AGENT, USER, 'hi');
    const first = MockWebSocket.instances[0];
    first.triggerOpen();
    first.triggerMessage({ type: 'run_started', run_id: 'r1' });

    // Passive disconnect (not a `complete` frame, not close()).
    first.triggerClose();
    expect(MockWebSocket.instances).toHaveLength(1); // not yet — backoff pending

    // After the first backoff (1s) a reconnect WS is opened.
    vi.advanceTimersByTime(1000);
    expect(MockWebSocket.instances).toHaveLength(2);
    // The reconnect handshake sends the run_id.
    const second = MockWebSocket.instances[1];
    second.triggerOpen();
    expect(second.sent.some((s) => s.includes('"run_id":"r1"'))).toBe(true);
  });

  it('does NOT reconnect when the socket dies before run_started', () => {
    wsManager.run(AGENT, USER, 'hi');
    const first = MockWebSocket.instances[0];
    first.triggerOpen();
    // No run_started → no run_id captured.
    first.triggerClose();
    vi.advanceTimersByTime(60000);
    expect(MockWebSocket.instances).toHaveLength(1); // never reconnected
  });

  it('does NOT reconnect after an intentional close()', () => {
    wsManager.run(AGENT, USER, 'hi');
    const first = MockWebSocket.instances[0];
    first.triggerOpen();
    first.triggerMessage({ type: 'run_started', run_id: 'r1' });

    wsManager.close(AGENT); // intentional teardown
    vi.advanceTimersByTime(60000);
    expect(MockWebSocket.instances).toHaveLength(1); // no retry
  });

  it('does NOT reconnect after a normal complete frame', () => {
    wsManager.run(AGENT, USER, 'hi');
    const first = MockWebSocket.instances[0];
    first.triggerOpen();
    first.triggerMessage({ type: 'run_started', run_id: 'r1' });
    first.triggerMessage({ type: 'complete' });
    first.triggerClose(); // server closes after complete
    vi.advanceTimersByTime(60000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('run_reconnect injects the user bubble on a fresh-tab reconnect', () => {
    useChatStore.getState().clearAll();
    wsManager.reconnect(AGENT, USER, 'r_inj1');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_inj1',
      state: 'running',
      input_content: 'hello there',
      input_timestamp: '2026-06-10T00:00:00',
    });

    const msgs = useChatStore.getState().agentSessions[AGENT]?.messages ?? [];
    expect(msgs.filter((m) => m.role === 'user' && m.content === 'hello there')).toHaveLength(1);
  });

  it('run_reconnect marks the session as a RESUMED run anchored to started_at (UTC-naive)', () => {
    // Shenzhen-r2 B1: a refresh mid-run replays the whole stream with no
    // "this is the same run continuing" signal — testers logged it as a
    // re-generation. run_reconnect carries started_at; the session must
    // keep it so the UI can say "resumed · running for N min".
    useChatStore.getState().clearAll();
    const nowMs = Date.parse('2026-08-14T16:08:00Z');
    vi.setSystemTime(nowMs);
    wsManager.reconnect(AGENT, USER, 'r_res1');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_res1',
      state: 'running',
      // Backend _format_dt emits the DB's NAIVE-UTC datetime, no offset —
      // Date.parse alone would read it as LOCAL time and skew the elapsed
      // display by the user's UTC offset.
      started_at: '2026-08-14T16:00:41.109740',
      input_content: 'long question',
      input_timestamp: '2026-08-14T16:00:41',
    });
    const resumed = useChatStore.getState().agentSessions[AGENT]?.resumedRun;
    expect(resumed?.runId).toBe('r_res1');
    expect(resumed?.startedAtMs).toBe(Date.parse('2026-08-14T16:00:41.109740Z'));
  });

  it('a run_reconnect for an ALREADY-FINISHED run never marks resumedRun', () => {
    // review #349 I2: the backend sends run_reconnect for any attachable
    // run — the already-finished case is the COMMON one (the run usually
    // ended during the very outage that triggered the reconnect). Badging
    // its replay "resumed · running for N min" is the same misread the
    // chip exists to kill, in the opposite direction.
    useChatStore.getState().clearAll();
    wsManager.reconnect(AGENT, USER, 'r_done1');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_done1',
      state: 'completed',
      started_at: '2026-08-14T16:00:41.109740',
    });
    expect(useChatStore.getState().agentSessions[AGENT]?.resumedRun).toBeNull();
  });

  it('an explicit offset in started_at is honored as-is, and a fresh run clears resumedRun', () => {
    useChatStore.getState().clearAll();
    wsManager.reconnect(AGENT, USER, 'r_res2');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_res2',
      state: 'running',
      started_at: '2026-08-14T17:00:41+01:00',
    });
    expect(useChatStore.getState().agentSessions[AGENT]?.resumedRun?.startedAtMs).toBe(
      Date.parse('2026-08-14T17:00:41+01:00'),
    );
    // a NEW fresh run must not inherit the resumed badge — mirror the real
    // send flow: ChatPanel calls startStreaming BEFORE wsManager.run
    useChatStore.getState().startStreaming(AGENT);
    wsManager.run(AGENT, USER, 'next question');
    MockWebSocket.instances[1].triggerOpen();
    expect(useChatStore.getState().agentSessions[AGENT]?.resumedRun).toBeNull();
  });

  it('run_reconnect does NOT duplicate the prompt the session already holds (event_id match)', () => {
    // Simulate the fresh-run flow that precedes an auto-reconnect:
    // the user sent the message in THIS tab and run_started stamped it.
    useChatStore.getState().clearAll();
    useChatStore.getState().addUserMessage(AGENT, 'hello there');
    useChatStore.getState().startStreaming(AGENT);
    useChatStore.getState().setCurrentRunId(AGENT, 'r_inj2');

    wsManager.reconnect(AGENT, USER, 'r_inj2');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_inj2',
      state: 'running',
      input_content: 'hello there',
      input_timestamp: '2026-06-10T00:00:00',
    });

    const msgs = useChatStore.getState().agentSessions[AGENT]?.messages ?? [];
    expect(msgs.filter((m) => m.role === 'user' && m.content === 'hello there')).toHaveLength(1);
  });

  it('run_reconnect does NOT duplicate a trailing event-id-less prompt with the same content', () => {
    // Same as above but the socket died BEFORE run_started — the local
    // user message never got its event_id backfilled.
    useChatStore.getState().clearAll();
    useChatStore.getState().addUserMessage(AGENT, 'hello there');
    useChatStore.getState().startStreaming(AGENT);

    wsManager.reconnect(AGENT, USER, 'r_inj3');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({
      type: 'run_reconnect',
      run_id: 'r_inj3',
      state: 'running',
      input_content: 'hello there',
      input_timestamp: '2026-06-10T00:00:00',
    });

    const msgs = useChatStore.getState().agentSessions[AGENT]?.messages ?? [];
    expect(msgs.filter((m) => m.role === 'user' && m.content === 'hello there')).toHaveLength(1);
  });

  it('terminal reconnect error (NotFound) stops streaming and does not retry', () => {
    useChatStore.getState().clearAll();
    wsManager.reconnect(AGENT, USER, 'r_gone');
    const ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    expect(useChatStore.getState().isAgentStreaming(AGENT)).toBe(true);

    ws.triggerMessage({
      type: 'error',
      error_type: 'NotFound',
      error_message: "Run 'r_gone' not found",
    });
    ws.triggerClose(); // server closes right after the error frame

    vi.advanceTimersByTime(120_000);
    expect(MockWebSocket.instances).toHaveLength(1); // no reconnect loop
    expect(useChatStore.getState().isAgentStreaming(AGENT)).toBe(false); // spinner cleared
  });

  it('backoff grows across repeated passive disconnects', () => {
    wsManager.run(AGENT, USER, 'hi');
    let ws = MockWebSocket.instances[0];
    ws.triggerOpen();
    ws.triggerMessage({ type: 'run_started', run_id: 'r1' });

    // 1st passive close → reconnect after 1s
    ws.triggerClose();
    vi.advanceTimersByTime(1000);
    expect(MockWebSocket.instances).toHaveLength(2);

    // 2nd passive close (reconnect WS dies before re-attach) → next backoff 2s
    ws = MockWebSocket.instances[1];
    ws.triggerClose();
    vi.advanceTimersByTime(1000); // not enough
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1000); // now 2s elapsed
    expect(MockWebSocket.instances).toHaveLength(3);
  });
});

describe('translateReconnectFrame — replayed tool frame fallback step id', () => {
  // When a replayed tool_call/tool_output frame's payload can't be parsed,
  // the synthesized progress falls back to a step id. It must NOT be the bare
  // run-agent phase id '3.4': currentSteps upsert by step id, so a bare '3.4'
  // would overwrite the real run-agent PHASE row (a tool_output carries
  // status:'completed' → "Agent 运行中… ✓" mid-run). The fallback must be a
  // '3.4.x'-shaped sub-step: parseFloat still 3.4 (loop logic unchanged) but
  // NOT in PHASE_STEP_IDS, so it renders as a tool row, never a phase row.
  // Pinning the CONSTRAINT, not the literal '3.4.replay' spelling.
  it.each(['tool_call', 'tool_output'] as const)(
    'an unparseable %s replay frame falls back to a non-phase step id',
    (kind) => {
      const frame = translateReconnectFrame({
        type: 'replay',
        kind,
        payload: 'not-json{',
      }) as { step: string } | null;
      expect(frame).not.toBeNull();
      expect(frame!.step).not.toBe('3.4');
      expect(PHASE_STEP_IDS.has(frame!.step)).toBe(false);
      // Still parses to the run-agent numeric band, so phaseDone logic is
      // unchanged — only the phase-row collision is avoided.
      expect(parseFloat(frame!.step)).toBe(3.4);
    },
  );
});

describe('translateReconnectFrame · narration tier', () => {
  it('a monologue segment replays as a tier-true thinking frame', () => {
    const out = translateReconnectFrame({
      type: 'replay',
      kind: 'thinking_segment_monologue',
      payload: 'Reading the config now.',
    }) as { thinking_content: string; monologue?: string };

    // monologue must repeat the text, not be a bool: the frontend predicate
    // is `monologue === thinking_content`.
    expect(out.thinking_content).toBe('Reading the config now.');
    expect(out.monologue).toBe('Reading the config now.');
  });

  it('a plain segment replays receded — including rows written before the tier existed', () => {
    const out = translateReconnectFrame({
      type: 'replay',
      kind: 'thinking_segment',
      payload: 'weighing options',
    }) as { thinking_content: string; monologue?: string };

    expect(out.thinking_content).toBe('weighing options');
    expect(out.monologue).toBeUndefined();
  });

  it('the in-flight partial carries its tier too', () => {
    const narrated = translateReconnectFrame({
      type: 'thinking_partial_replay',
      content: 'Checking the plug',
      monologue: true,
    }) as { monologue?: string };
    const reasoned = translateReconnectFrame({
      type: 'thinking_partial_replay',
      content: 'weighing',
    }) as { monologue?: string };

    expect(narrated.monologue).toBe('Checking the plug');
    expect(reasoned.monologue).toBeUndefined();
  });
});
