import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { wsManager } from '../wsManager';
import { useChatStore } from '@/stores/chatStore';

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
