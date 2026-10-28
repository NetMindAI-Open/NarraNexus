/**
 * @file_name: client.test.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: ChatClient: history maps events to user/assistant messages with the right identity headers; a turn opens the run socket with the app's payload, accumulates both delta types, ends on complete/error/cancelled, and stop sends the stop action.
 */
import { describe, expect, it, vi } from 'vitest';

import { ChatClient, historyToMessages, wsUrl } from '../client';

class FakeSocket {
  static instances: FakeSocket[] = [];
  url: string;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
  }
  emit(msg: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

const EVENTS = [
  { event_id: 'e1', trigger: 'hi', final_output: 'hello!', created_at: '2026-09-04T00:00:00Z' },
  { event_id: 'e2', trigger: '', final_output: 'unprompted', created_at: '2026-09-04T00:01:00Z' },
];

describe('ChatClient', () => {
  it('maps history events to messages and sends the identity header', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://x/api/agents/a1/chat-history?event_limit=5');
      expect((init?.headers as Record<string, string>)['X-User-Id']).toBe('u1');
      return { ok: true, json: async () => ({ events: EVENTS }) } as Response;
    });
    const client = new ChatClient({ baseUrl: 'http://x', agentId: 'a1', userId: 'u1', fetch: fetchMock as unknown as typeof fetch });
    const msgs = await client.history(5);
    expect(msgs.map((m) => [m.role, m.content])).toEqual([
      ['user', 'hi'],
      ['assistant', 'hello!'],
      ['assistant', 'unprompted'],
    ]);
    expect(historyToMessages([])).toEqual([]);
  });

  it('uses the bearer on cloud and the x_user_id query locally', () => {
    expect(wsUrl('https://h', 'u1')).toBe('wss://h/ws/agent/run?x_user_id=u1');
    expect(wsUrl('https://h', 'u1', 'jwt')).toBe('wss://h/ws/agent/run');
  });

  it('streams a turn: payload, both delta kinds, complete, stop', async () => {
    const client = new ChatClient({ baseUrl: 'http://x', agentId: 'a1', userId: 'u1', webSocket: FakeSocket as unknown as typeof WebSocket });
    const events: unknown[] = [];
    const handle = client.send('question', (e) => events.push(e));
    await Promise.resolve();
    const sock = FakeSocket.instances.at(-1)!;
    expect(sock.url).toBe('ws://x/ws/agent/run?x_user_id=u1');
    expect(JSON.parse(sock.sent[0])).toEqual({ agent_id: 'a1', user_id: 'u1', input_content: 'question', working_source: 'chat' });
    sock.emit({ type: 'thinking', content: 'ignored' });
    sock.emit({ type: 'agent_response', delta: 'Hel', response_type: 'text' });
    sock.emit({ type: 'agent_reply_delta', delta: 'lo', call_id: 'c1', tool_name: 'reply' });
    handle.stop();
    expect(JSON.parse(sock.sent[1])).toEqual({ action: 'stop' });
    sock.emit({ type: 'complete' });
    expect(await handle.done).toBe('Hello');
    expect(events).toEqual([
      { type: 'delta', text: 'Hel' },
      { type: 'delta', text: 'lo' },
      { type: 'complete', text: 'Hello' },
    ]);
    expect(sock.closed).toBe(true);
    sock.emit({ type: 'error', error_message: 'late' }); // after settle: ignored
    expect(events).toHaveLength(3);
  });

  it('reports error codes and cancellation, and a silent close as an error', async () => {
    const client = new ChatClient({ agentId: 'a1', userId: 'u1', token: 'jwt', baseUrl: 'http://x', webSocket: FakeSocket as unknown as typeof WebSocket });
    const seen: unknown[] = [];
    const h1 = client.send('x', (e) => seen.push(e));
    await Promise.resolve();
    const s1 = FakeSocket.instances.at(-1)!;
    expect(JSON.parse(s1.sent[0]).token).toBe('jwt');
    s1.emit({ type: 'error', error_message: 'nope', error_code: 'quota' });
    await h1.done;
    expect(seen).toEqual([{ type: 'error', message: 'nope', code: 'quota' }]);
    const h2 = client.send('y', (e) => seen.push(e));
    await Promise.resolve();
    FakeSocket.instances.at(-1)!.emit({ type: 'cancelled' });
    await h2.done;
    const h3 = client.send('z', (e) => seen.push(e));
    await Promise.resolve();
    FakeSocket.instances.at(-1)!.onclose?.();
    await h3.done;
    expect(seen.slice(1)).toEqual([{ type: 'cancelled' }, { type: 'error', message: 'connection closed' }]);
  });

  it('a socket close mid-answer (no complete/error/cancelled frame) reports `interrupted`, not `complete` — I-7', async () => {
    // The backend does not cancel the run on WS disconnect; a partial answer that streamed
    // in before the drop must never be reported as `complete`, or the caller renders a
    // truncated reply as if it were the finished answer.
    const client = new ChatClient({ agentId: 'a1', userId: 'u1', baseUrl: 'http://x', webSocket: FakeSocket as unknown as typeof WebSocket });
    const seen: unknown[] = [];
    const handle = client.send('question', (e) => seen.push(e));
    await Promise.resolve();
    const sock = FakeSocket.instances.at(-1)!;
    sock.emit({ type: 'agent_response', delta: 'partial answer' });
    sock.onclose?.(); // network drop: no complete/error/cancelled frame ever arrives
    expect(await handle.done).toBe('partial answer');
    expect(seen).toEqual([{ type: 'delta', text: 'partial answer' }, { type: 'interrupted', text: 'partial answer' }]);
  });
});
