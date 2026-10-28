/**
 * @file_name: client.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Framework-agnostic chat transport for embedding: history over `/api/agents/{id}/chat-history`
 * and a turn over the `/ws/agent/run` WebSocket (same protocol the app's chat uses: `agent_response` /
 * `agent_reply_delta` deltas, `complete`, `error`, `cancelled`). Local deployments identify the user with the
 * `X-User-Id` header and the `?x_user_id=` WS query; cloud deployments send the bearer token.
 */
export interface ChatClientOptions {
  /** Backend origin, e.g. `http://localhost:8000`; empty = same origin. */
  baseUrl?: string;
  agentId: string;
  userId: string;
  /** Cloud JWT; omit on local deployments. */
  token?: string;
  /** Test seam: the WebSocket constructor to use. */
  webSocket?: typeof WebSocket;
  /** Test seam: fetch to use. */
  fetch?: typeof fetch;
}

export interface ChatTurnMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
}

export type TurnEvent =
  | { type: 'delta'; text: string }
  | { type: 'complete'; text: string }
  | { type: 'error'; message: string; code?: string }
  | { type: 'cancelled' }
  /**
   * The socket closed without a `complete`/`error`/`cancelled` frame while
   * text had already streamed in (network drop, proxy/idle timeout, tab
   * suspend). The backend does NOT cancel the run on WS disconnect — the
   * agent keeps running and writes its full answer to the turn record —
   * so `text` here is a partial view, not the final answer. Distinguishing
   * this from `complete` matters: showing a truncated reply as "done" is
   * exactly the "content looks lost" failure this event exists to avoid.
   */
  | { type: 'interrupted'; text: string };

export interface TurnHandle {
  /**
   * Ask the backend to stop the run. The socket does NOT answer with a
   * `cancelled` frame on `/ws/agent/run` — the backend acknowledges a stop
   * request with `{"type":"stopping"}` (not currently surfaced as a
   * `TurnEvent`); a `cancelled` frame has never been observed on this
   * endpoint. `done` will settle via `interrupted` or `error` once the
   * socket closes, not via a `cancelled` event.
   */
  stop(): void;
  /** Resolves when the turn ended (complete, error, cancelled, or interrupted). */
  done: Promise<string>;
}

interface HistoryEvent {
  event_id: string;
  trigger: string;
  final_output: string;
  created_at: string;
}

export function historyToMessages(events: HistoryEvent[]): ChatTurnMessage[] {
  const out: ChatTurnMessage[] = [];
  for (const ev of events) {
    const at = Date.parse(ev.created_at) || 0;
    if (ev.trigger) out.push({ id: `${ev.event_id}:user`, role: 'user', content: ev.trigger, createdAt: at });
    if (ev.final_output) out.push({ id: `${ev.event_id}:assistant`, role: 'assistant', content: ev.final_output, createdAt: at });
  }
  return out;
}

export function wsUrl(baseUrl: string, userId: string, token?: string): string {
  const origin = baseUrl || (typeof window !== 'undefined' ? window.location.origin : '');
  const ws = origin.replace(/^http(s?):\/\//i, (_m, s) => `ws${s}://`);
  return token ? `${ws}/ws/agent/run` : `${ws}/ws/agent/run?x_user_id=${encodeURIComponent(userId)}`;
}

export class ChatClient {
  private readonly opts: ChatClientOptions;

  constructor(opts: ChatClientOptions) {
    this.opts = opts;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.opts.token) h.Authorization = `Bearer ${this.opts.token}`;
    else h['X-User-Id'] = this.opts.userId;
    return h;
  }

  async history(limit = 20): Promise<ChatTurnMessage[]> {
    const f = this.opts.fetch ?? fetch;
    const res = await f(`${this.opts.baseUrl ?? ''}/api/agents/${encodeURIComponent(this.opts.agentId)}/chat-history?event_limit=${limit}`, {
      headers: this.headers(),
    });
    if (!res.ok) throw new Error(`chat-history: HTTP ${res.status}`);
    const body = (await res.json()) as { events?: HistoryEvent[] };
    return historyToMessages(body.events ?? []);
  }

  /** Send one user message; `onEvent` receives deltas until the turn ends. */
  send(text: string, onEvent: (event: TurnEvent) => void): TurnHandle {
    const WS = this.opts.webSocket ?? WebSocket;
    const socket = new WS(wsUrl(this.opts.baseUrl ?? '', this.opts.userId, this.opts.token));
    let buffer = '';
    let settled = false;
    let resolveDone: (text: string) => void = () => {};
    const done = new Promise<string>((resolve) => {
      resolveDone = resolve;
    });
    const finish = (event: TurnEvent) => {
      if (settled) return;
      settled = true;
      onEvent(event);
      resolveDone(buffer);
      try {
        socket.close();
      } catch {
        /* already closed */
      }
    };
    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          agent_id: this.opts.agentId,
          user_id: this.opts.userId,
          input_content: text,
          working_source: 'chat',
          ...(this.opts.token ? { token: this.opts.token } : {}),
        }),
      );
    };
    socket.onmessage = (raw: MessageEvent) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(String(raw.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      switch (msg.type) {
        case 'agent_response':
        case 'agent_reply_delta': {
          const delta = typeof msg.delta === 'string' ? msg.delta : '';
          buffer += delta;
          onEvent({ type: 'delta', text: delta });
          return;
        }
        case 'complete':
          finish({ type: 'complete', text: buffer });
          return;
        case 'cancelled':
          finish({ type: 'cancelled' });
          return;
        case 'error':
          finish({ type: 'error', message: String(msg.error_message ?? 'error'), code: msg.error_code ? String(msg.error_code) : undefined });
          return;
        default:
          return; // thinking / tool_call / progress / heartbeat: not rendered by the widget
      }
    };
    socket.onerror = () => finish({ type: 'error', message: 'connection failed' });
    // Only a `complete` frame means the turn actually finished; once one arrives `settled`
    // is already true and this handler is a no-op (see `finish`'s guard). Reaching here with
    // text already buffered means the connection dropped mid-answer — report `interrupted`,
    // not `complete`, so the caller can distinguish "done" from "cut off".
    socket.onclose = () => finish(buffer ? { type: 'interrupted', text: buffer } : { type: 'error', message: 'connection closed' });
    return {
      stop: () => {
        try {
          socket.send(JSON.stringify({ action: 'stop' }));
        } catch {
          /* socket not open */
        }
      },
      done,
    };
  }
}
