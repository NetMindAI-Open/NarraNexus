import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { wsManager } from '../wsManager';

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
  constructor(url: string) { this.url = url; MockWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.(); }
  triggerOpen() { this.readyState = MockWebSocket.OPEN; this.onopen?.(); }
  triggerMessage(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}

const AGENT = 'agent_steer_ws';
const USER = 'u';

function startRun(steerable: boolean): MockWebSocket {
  wsManager.run(AGENT, USER, 'hi', 'Agent');
  const ws = MockWebSocket.instances.at(-1)!;
  ws.triggerOpen();
  ws.triggerMessage({ type: 'run_started', run_id: 'r1', steerable });
  ws.sent = []; // ignore the initial run payload
  return ws;
}

describe('wsManager.steer', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    MockWebSocket.instances = [];
  });
  afterEach(() => {
    wsManager.close(AGENT);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends a steer frame on a steerable run and reports it sent', () => {
    const ws = startRun(true);
    expect(wsManager.isSteerable(AGENT)).toBe(true);

    const ok = wsManager.steer(AGENT, 'also send the summary', 'c1');
    expect(ok).toBe(true);
    expect(JSON.parse(ws.sent[0])).toEqual({
      action: 'steer', input_content: 'also send the summary', client_msg_id: 'c1',
    });
  });

  it('does not send (returns false) when the run is not steerable', () => {
    const ws = startRun(false);
    expect(wsManager.isSteerable(AGENT)).toBe(false);
    const ok = wsManager.steer(AGENT, 'x', 'c2');
    expect(ok).toBe(false);
    expect(ws.sent.length).toBe(0);
  });

  it('returns false when there is no live connection', () => {
    expect(wsManager.steer('no_such_agent', 'x', 'c3')).toBe(false);
  });

  it('stops being steerable once the run completes (a finished run cannot drain a steer)', () => {
    const ws = startRun(true);
    expect(wsManager.isSteerable(AGENT)).toBe(true);
    ws.triggerMessage({ type: 'complete' });
    expect(wsManager.isSteerable(AGENT)).toBe(false);
    const ok = wsManager.steer(AGENT, 'too late', 'c4');
    expect(ok).toBe(false);
    expect(ws.sent.length).toBe(0);
  });
});
