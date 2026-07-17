/**
 * WebSocket connection manager — singleton service
 *
 * Manages multiple concurrent WebSocket connections (one per agent).
 * Decoupled from React component lifecycle so connections persist across agent switches.
 */

import { useChatStore } from '@/stores/chatStore';
import { useConfigStore } from '@/stores/configStore';
import { getWsBaseUrl } from '@/stores/runtimeStore';
import { MOCK_ENABLED } from '@/lib/mock';
import { dispatchAuthExpired, isAuthErrorMessage } from './wsAuthError';
import {
  circuitOpenReason,
  dispatchAgentCircuitOpen,
  isCircuitOpenMessage,
} from './wsCircuitOpen';
import type { Attachment, RuntimeMessage } from '@/types';

interface ConnectionEntry {
  ws: WebSocket;
  completed: boolean;
  // Auto-reconnect context (A3). Populated once the server sends
  // `run_started` so a passive disconnect (WiFi blip, laptop sleep) can
  // re-attach to the still-alive BackgroundRun via the Phase C reconnect
  // protocol instead of silently stranding a long-running agent.
  runId?: string;
  userId?: string;
  agentName?: string;
  onComplete?: OnCompleteCallback;
}

type OnCompleteCallback = (agentId: string) => void;

// Capped exponential backoff for passive-disconnect reconnects. NO attempt
// ceiling (iron rule #14: long-running agents are first-class — we keep
// retrying through an outage as long as the backend run may still be alive);
// the cap just stops a tight loop.
const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

class WebSocketManager {
  private connections = new Map<string, ConnectionEntry>();
  private onCompleteCallbacks = new Map<string, OnCompleteCallback>();
  // Pending backoff timers for auto-reconnect, keyed by agentId, so an
  // intentional close() can cancel a scheduled retry.
  private reconnectTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private reconnectAttempts = new Map<string, number>();

  /** Start a new agent run via WebSocket */
  run(
    agentId: string,
    userId: string,
    inputContent: string,
    options?: {
      onComplete?: OnCompleteCallback;
      agentName?: string;
      attachments?: Attachment[];
    },
  ): void {
    // Close existing connection for this agent if any
    this.close(agentId);

    if (options?.onComplete) {
      this.onCompleteCallbacks.set(agentId, options.onComplete);
    }

    const agentName = options?.agentName;

    // Mock mode: simulate a simple turn (assistant echoes back) instead of
    // opening a real socket. Keeps chat UI interactive for visual review.
    if (MOCK_ENABLED) {
      this.runMocked(agentId, userId, inputContent);
      return;
    }

    // Resolve WebSocket URL from the single source of truth (runtimeStore).
    // Local mode: ws://localhost:8000/ws/...  Cloud mode: ws://<cloud-host>/ws/...
    // Both derive from the same base URL as REST API calls, so if the
    // mode switches between turns the next connection picks up the new host.
    //
    // Identity anchor: in local mode the backend now requires
    // ``?x_user_id=<id>`` on the URL and rejects the connection if the
    // payload ``user_id`` doesn't match. Browsers can't set custom WS
    // headers, so the URL query string is the only place a server-side
    // identity anchor can live. Cloud mode ignores this — it uses the
    // JWT inside the first payload.
    const wsUrl = `${getWsBaseUrl()}/ws/agent/run?x_user_id=${encodeURIComponent(userId)}`;
    const ws = new WebSocket(wsUrl);

    const entry: ConnectionEntry = {
      ws,
      completed: false,
      userId,
      agentName,
      onComplete: options?.onComplete,
    };
    this.connections.set(agentId, entry);
    // A fresh run() supersedes any pending reconnect backoff for this agent.
    this._clearReconnectTimer(agentId);
    this.reconnectAttempts.delete(agentId);

    const store = useChatStore.getState;

    ws.onopen = () => {
      // Include JWT token in first message — cloud mode requires it,
      // local mode ignores it. Browser WebSocket API can't set custom
      // headers, so auth piggy-backs on the existing request payload.
      const token = useConfigStore.getState().token;
      ws.send(JSON.stringify({
        agent_id: agentId,
        user_id: userId,
        input_content: inputContent,
        working_source: 'chat',
        token: token || undefined,
        attachments: options?.attachments && options.attachments.length > 0
          ? options.attachments
          : undefined,
      }));
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as RuntimeMessage;

        // Skip heartbeats
        if (message.type === 'heartbeat') return;

        // Symmetric with REST 401: a cloud JWT can expire mid-session
        // or land stale after a dmg upgrade. Backend closes the socket
        // with a {type:'error', error_type:'AuthError', ...} frame —
        // surface that as the app-wide auth-expired event so App.tsx
        // can logout + show the "session expired" banner instead of
        // letting the user see only a red "Token expired" chat bubble
        // with no path to re-login.
        if (isAuthErrorMessage(message)) {
          dispatchAuthExpired();
          return;
        }

        // Circuit-breaker open: the backend refused to START this fresh run
        // (agent paused after repeated auth/quota failures, or briefly
        // cooling) and is closing the socket. Surface a banner with a
        // "Resume" action (App.tsx) instead of a dead-end red bubble, and
        // clear the spinner so the composer isn't stuck streaming.
        if (isCircuitOpenMessage(message)) {
          dispatchAgentCircuitOpen({ agentId, reason: circuitOpenReason(message) });
          entry.completed = true;
          this.onCompleteCallbacks.delete(agentId);
          store().processMessage(agentId, message);
          store().stopStreaming(agentId, agentName);
          return;
        }

        // Capture run_id so a later passive disconnect can re-attach to
        // the still-alive BackgroundRun (A3). A successful run also clears
        // the backoff counter — this connection reached the server fine.
        if (message.type === 'run_started' && message.run_id) {
          entry.runId = message.run_id;
          this.reconnectAttempts.delete(agentId);
        }

        store().processMessage(agentId, message);

        if (message.type === 'complete') {
          entry.completed = true;
          const cb = this.onCompleteCallbacks.get(agentId);
          cb?.(agentId);
          this.onCompleteCallbacks.delete(agentId);
        }
      } catch (e) {
        console.error(`[wsManager] Failed to parse message for ${agentId}:`, e);
      }
    };

    ws.onerror = (error) => {
      console.error(`[wsManager] WebSocket error for ${agentId}:`, error);
    };

    ws.onclose = () => {
      // Use closure-captured `entry` — NOT this.connections.get(agentId).
      // After close() or re-run(), the map may already be cleared or hold a NEW entry
      // for the same agentId. Reading from map would check the wrong entry.
      if (this.connections.get(agentId) === entry) {
        this.connections.delete(agentId);
      }
      this._handleUnexpectedClose(agentId, entry);
    };
  }

  /**
   * Common passive-disconnect handler for both run() and reconnect() WS.
   *
   * If the close was intentional (completed === true, set by a `complete`
   * frame or close()), do nothing. Otherwise, if we captured a run_id, the
   * backend BackgroundRun is (iron rule #14) very likely still alive — so
   * schedule a backoff reconnect rather than stranding the user on a frozen
   * "stopped" UI. Only when there's no run_id to reconnect with (the socket
   * died before `run_started`) do we fall back to stopStreaming.
   */
  private _handleUnexpectedClose(agentId: string, entry: ConnectionEntry): void {
    if (entry.completed) return;

    if (entry.runId && entry.userId) {
      console.warn(`[wsManager] passive disconnect for ${agentId} — scheduling reconnect`);
      this._scheduleReconnect(agentId, entry);
    } else {
      // No live run to re-attach to — surface the stop as before.
      console.warn(`[wsManager] WebSocket closed unexpectedly for ${agentId} (no run_id, not reconnecting)`);
      useChatStore.getState().stopStreaming(agentId, entry.agentName);
    }
  }

  private _clearReconnectTimer(agentId: string): void {
    const t = this.reconnectTimers.get(agentId);
    if (t !== undefined) {
      clearTimeout(t);
      this.reconnectTimers.delete(agentId);
    }
  }

  private _scheduleReconnect(agentId: string, entry: ConnectionEntry): void {
    // Cancel any already-pending retry (avoid stacking timers).
    this._clearReconnectTimer(agentId);

    const attempt = this.reconnectAttempts.get(agentId) ?? 0;
    const delay = RECONNECT_BACKOFF_MS[Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)];
    this.reconnectAttempts.set(agentId, attempt + 1);

    const timer = setTimeout(() => {
      this.reconnectTimers.delete(agentId);
      // If something re-ran or closed this agent in the meantime, the
      // connections map will hold a different (or no) entry; bail out.
      if (this.connections.has(agentId)) return;
      console.info(`[wsManager] reconnect attempt ${attempt + 1} for ${agentId} (run ${entry.runId})`);
      this.reconnect(agentId, entry.userId!, entry.runId!, {
        agentName: entry.agentName,
        onComplete: entry.onComplete,
      });
    }, delay);
    this.reconnectTimers.set(agentId, timer);
  }

  /**
   * Reconnect to an existing in-flight (or just-finished) agent run.
   *
   * Phase C protocol: the backend keeps the BackgroundRun task alive
   * independently of any single WebSocket (iron rule #14). When the
   * client reconnects with ``run_id``, the server replays every
   * event_stream row in seq order then — if the run is still alive —
   * subscribes the WS to the live Broadcaster for continuation.
   *
   * From the user's perspective this is "open the tab, see everything
   * the agent did while I was away, then keep seeing whatever it
   * does next" — the user-experience equivalent of "never having
   * closed the tab".
   *
   * Server-side messages we translate here:
   *   - run_reconnect             metadata (state, started_at, etc.)
   *   - thinking_partial_replay   current_thinking_buffer snapshot
   *   - replay (kind, seq, payload)  history events in seq ASC order
   *   - run_ended                 terminal frame (state, final_output)
   *   - and the usual live frames (agent_thinking / agent_response /
   *     progress / error / stopping / cancelled / heartbeat / complete)
   *
   * The translation layer below maps each replay payload back into
   * the same RuntimeMessage shape live frames carry, so chatStore
   * processMessage doesn't need to know history-vs-live.
   */
  reconnect(
    agentId: string,
    userId: string,
    runId: string,
    options?: {
      onComplete?: OnCompleteCallback;
      agentName?: string;
    },
  ): void {
    // Close any existing connection for this agent first — the same
    // contract as run() so we never have two WS open per agent.
    this.close(agentId);

    if (options?.onComplete) {
      this.onCompleteCallbacks.set(agentId, options.onComplete);
    }

    const agentName = options?.agentName;

    if (MOCK_ENABLED) {
      // Mock mode has no persistent BackgroundRun to subscribe to;
      // we just no-op reconnect.
      console.info('[wsManager] mock mode — reconnect is a no-op');
      return;
    }

    // Same identity anchor as run() — backend cross-checks payload.user_id
    // against ?x_user_id= in local mode.
    const wsUrl = `${getWsBaseUrl()}/ws/agent/run?x_user_id=${encodeURIComponent(userId)}`;
    const ws = new WebSocket(wsUrl);

    // Carry reconnect context so a passive disconnect of the reconnect WS
    // itself re-schedules another backoff retry (a flapping network keeps
    // retrying, iron rule #14).
    const entry: ConnectionEntry = {
      ws,
      completed: false,
      runId,
      userId,
      agentName,
      onComplete: options?.onComplete,
    };
    this.connections.set(agentId, entry);

    const store = useChatStore.getState;

    ws.onopen = () => {
      const token = useConfigStore.getState().token;
      ws.send(JSON.stringify({
        run_id: runId,
        user_id: userId,
        token: token || undefined,
      }));
      // Mark the local session as streaming so AgentList spinner and
      // any in-flight UI cues stay consistent through the replay.
      store().startStreaming(agentId);
    };

    ws.onmessage = (event) => {
      try {
        const raw = JSON.parse(event.data);

        if (raw.type === 'heartbeat') return;

        // Same auth-expired bridge as the run() path — JWT can also
        // be stale at reconnect time (user reopened the tab a week
        // later). See wsAuthError.ts for the rationale.
        if (isAuthErrorMessage(raw)) {
          dispatchAuthExpired();
          return;
        }

        // Terminal reconnect-protocol errors: the server told us this
        // run_id can never be streamed by this client (row missing,
        // not ours, lookup broke) and is about to close the socket.
        // Without this branch the close is treated as a passive
        // disconnect → infinite backoff reconnect loop against the
        // same dead run_id, with isStreaming stuck true (the spinner
        // that only a page refresh cleared). Precise error_type match
        // only — transient/replayed agent errors keep flowing into the
        // chat surface as before.
        if (
          raw.type === 'error' &&
          (raw.error_type === 'NotFound' ||
            raw.error_type === 'Forbidden' ||
            raw.error_type === 'DBError')
        ) {
          entry.completed = true;
          this.onCompleteCallbacks.delete(agentId);
          store().processMessage(agentId, raw as RuntimeMessage);
          store().stopStreaming(agentId, agentName);
          return;
        }

        // Phase C: inject the user message that triggered this run
        // BEFORE any replay frames render. Backend hands us
        // ``input_content`` (from events.env_context.input) and
        // ``input_timestamp`` (events.created_at). The timestamp is
        // the same value ChatModule.hook_after_event_execution
        // eventually writes into agent_messages.user_ts, so once the
        // turn completes and history reloads, ChatPanel's existing
        // role:content + 60s timestamp-proximity dedup collapses
        // the injected bubble with the persisted history row — no
        // duplicate "user said X" rendering.
        //
        // Injection is IDEMPOTENT against the local session: an
        // auto-reconnect (passive disconnect of the fresh-run WS in
        // THIS tab) re-attaches to a run whose prompt the session
        // already holds — either stamped with this run's event_id
        // (run_started arrived before the drop) or still trailing
        // event-id-less with identical content (drop before
        // run_started). Injecting again rendered the user's message
        // twice until the next history reload collapsed it.
        if (raw.type === 'run_reconnect') {
          const inputContent = (raw.input_content as string | null | undefined) ?? '';
          if (inputContent) {
            const session = useChatStore.getState().agentSessions[agentId];
            const msgs = session?.messages ?? [];
            const last = msgs.length > 0 ? msgs[msgs.length - 1] : undefined;
            const alreadyInSession =
              msgs.some((m) => m.role === 'user' && m.event_id === runId) ||
              (!!last && last.role === 'user' && !last.event_id && last.content === inputContent);
            if (!alreadyInSession) {
              const tsStr = raw.input_timestamp as string | null | undefined;
              const tsMs = tsStr ? Date.parse(tsStr) : NaN;
              store().addUserMessage(
                agentId,
                inputContent,
                undefined,
                Number.isFinite(tsMs) ? tsMs : undefined,
              );
            }
          }
          // Successfully re-attached — reset the backoff counter so a
          // later disconnect starts fresh at the shortest delay (A3).
          this.reconnectAttempts.delete(agentId);
          // Record the run id (and backfill it onto the user message just
          // added) so the reconnected turn's messages dedup against the
          // persisted history rows by exact (role, event_id). The fresh-run
          // path does this via the `run_started` frame in chatStore; the
          // reconnect protocol absorbs `run_reconnect` before processMessage
          // (translateReconnectFrame returns null), so we set it here.
          store().setCurrentRunId(agentId, runId);
        }

        // Honor terminal lifecycle frames BEFORE the translate/early-return
        // guard below. `run_ended` is the terminal frame the backend sends
        // when the run we reconnected to has ALREADY finished — which is the
        // common case, since the run typically finished during the very
        // outage that triggered this reconnect. translateReconnectFrame()
        // absorbs `run_ended` to null (it's protocol metadata), so if the
        // `translated === null` guard ran first we'd `return` without ever
        // clearing `isStreaming` — leaving the "Acting…" spinner spinning
        // until a manual page refresh. `complete` is a live frame that still
        // reaches stopStreaming via processMessage below, so we only have to
        // stop the stream ourselves for `run_ended`.
        if (raw.type === 'run_ended' || raw.type === 'complete') {
          entry.completed = true;
          const cb = this.onCompleteCallbacks.get(agentId);
          cb?.(agentId);
          this.onCompleteCallbacks.delete(agentId);
          if (raw.type === 'run_ended') {
            store().stopStreaming(agentId, agentName);
          }
        }

        // Translate Phase C reconnect-mode frames into RuntimeMessage
        // shapes the existing chatStore.processMessage already knows
        // how to render. live frames pass through untouched.
        const translated = translateReconnectFrame(raw);
        if (translated === null) return;

        store().processMessage(agentId, translated as RuntimeMessage);
      } catch (e) {
        console.error(`[wsManager] Failed to parse reconnect message for ${agentId}:`, e);
      }
    };

    ws.onerror = (error) => {
      console.error(`[wsManager] reconnect WS error for ${agentId}:`, error);
    };

    ws.onclose = () => {
      if (this.connections.get(agentId) === entry) {
        this.connections.delete(agentId);
      }
      // Passive disconnect of the reconnect WS → schedule another backoff
      // retry (a flapping network keeps retrying, iron rule #14).
      this._handleUnexpectedClose(agentId, entry);
    };
  }

  /**
   * Send a stop signal to gracefully cancel the running agent loop.
   *
   * The backend's dual-task WebSocket handler listens for this message
   * and triggers the CancellationToken, which propagates through the
   * entire execution pipeline including killing the Claude CLI subprocess.
   */
  stop(agentId: string): void {
    if (MOCK_ENABLED) {
      useChatStore.getState().stopStreaming(agentId);
      return;
    }
    const entry = this.connections.get(agentId);
    if (entry && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({ action: 'stop' }));
    }
  }

  /** Close a specific agent's connection */
  close(agentId: string): void {
    const entry = this.connections.get(agentId);
    if (entry) {
      entry.completed = true; // Mark as intentional close
      entry.ws.close();
      this.connections.delete(agentId);
      this.onCompleteCallbacks.delete(agentId);
    }
  }

  /** Close all connections */
  closeAll(): void {
    for (const [agentId] of this.connections) {
      this.close(agentId);
    }
  }

  /** Check if an agent has an active connection */
  isRunning(agentId: string): boolean {
    return this.connections.has(agentId);
  }

  /**
   * Mock-mode stream simulator — drives the chat store with a fake but
   * realistic sequence of messages so chat UI renders without a backend.
   * Fires progress → tool_call → thinking → streaming text → complete.
   */
  private runMocked(agentId: string, _userId: string, inputContent: string): void {
    const store = useChatStore.getState;
    const push = (msg: RuntimeMessage) => store().processMessage(agentId, msg);
    const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

    const reply =
      `Got it. You said: "${inputContent.slice(0, 80)}${inputContent.length > 80 ? '…' : ''}".\n\n` +
      `This is a **mock reply** — the frontend is running with \`?mock=1\`. ` +
      `You can test message rendering, streaming cursor, markdown, tool-call panels, and long-message wrapping without a live backend.`;

    (async () => {
      await delay(150);
      push({ type: 'progress', timestamp: Date.now(), step: '0', title: 'Understand', description: 'Parsing user intent', status: 'running', substeps: [] });
      await delay(400);
      push({ type: 'progress', timestamp: Date.now(), step: '0', title: 'Understand', description: 'Parsing user intent', status: 'completed', substeps: [] });

      push({ type: 'progress', timestamp: Date.now(), step: '1', title: 'Plan', description: 'Choosing modules', status: 'running', substeps: [] });
      await delay(350);
      push({ type: 'agent_thinking', timestamp: Date.now(), thinking_content: 'Simple echo request — no tools needed. Draft a friendly reply and note mock-mode status.' });
      await delay(300);
      push({ type: 'progress', timestamp: Date.now(), step: '1', title: 'Plan', description: 'Choosing modules', status: 'completed', substeps: [] });

      push({ type: 'progress', timestamp: Date.now(), step: '2', title: 'Respond', description: 'Streaming text', status: 'running', substeps: [] });
      await delay(200);
      const chunks = reply.match(/.{1,24}/gs) ?? [reply];
      for (const chunk of chunks) {
        push({ type: 'agent_response', timestamp: Date.now(), response_type: 'text', delta: chunk });
        await delay(35);
      }
      push({ type: 'progress', timestamp: Date.now(), step: '2', title: 'Respond', description: 'Streaming text', status: 'completed', substeps: [] });
      await delay(120);
      push({ type: 'complete', timestamp: Date.now(), message: 'done' });

      const cb = this.onCompleteCallbacks.get(agentId);
      cb?.(agentId);
      this.onCompleteCallbacks.delete(agentId);
    })().catch((e) => console.warn('[wsManager] mock stream error', e));
  }
}

export const wsManager = new WebSocketManager();


/**
 * Map a Phase C reconnect-mode frame back to a RuntimeMessage that
 * chatStore.processMessage already knows. Returns null when the frame
 * is metadata-only (run_reconnect / run_ended) — those bypass the
 * normal message pipeline but are otherwise ignored at the store level.
 *
 * Frames passed through:
 *   - any frame with no special "replay" semantics (live agent_thinking,
 *     agent_response, progress, error, stopping, cancelled, complete, ...)
 *     is returned as-is.
 *
 * Frames translated:
 *   - thinking_partial_replay: { content }
 *       → agent_thinking { thinking_content: content }
 *   - replay: { kind, seq, payload }
 *       depending on kind, materialise the same RuntimeMessage shape
 *       that the live path would have emitted.
 *
 * Frames absorbed (return null):
 *   - run_reconnect, run_ended, reconnect_warning — these are protocol-
 *     level metadata the store doesn't need; we use them only as
 *     lifecycle signals via the onmessage caller.
 */
function translateReconnectFrame(raw: { [key: string]: unknown }): unknown | null {
  const t = raw.type as string | undefined;

  if (t === 'run_reconnect' || t === 'run_ended' || t === 'reconnect_warning') {
    return null;
  }

  if (t === 'thinking_partial_replay') {
    return {
      type: 'agent_thinking',
      timestamp: Date.now(),
      thinking_content: (raw.content as string) ?? '',
    };
  }

  if (t === 'replay') {
    const kind = raw.kind as string | undefined;
    const payloadRaw = raw.payload as string | null | undefined;
    const payload = payloadRaw ?? '';

    if (kind === 'thinking_segment') {
      return {
        type: 'agent_thinking',
        timestamp: Date.now(),
        thinking_content: payload,
      };
    }

    if (kind === 'text_delta') {
      return {
        type: 'agent_response',
        timestamp: Date.now(),
        response_type: 'text',
        delta: payload,
      };
    }

    if (kind === 'tool_call') {
      const p = safeParseJson(payload);
      return {
        type: 'progress',
        timestamp: Date.now(),
        step: (p?.step as string) ?? '3.4',
        title: (p?.title as string) ?? 'Tool call',
        description: 'Executing...',
        status: 'running',
        details: {
          tool_name: p?.tool_name,
          arguments: p?.arguments ?? {},
        },
        substeps: [],
      };
    }

    if (kind === 'tool_output') {
      const p = safeParseJson(payload);
      return {
        type: 'progress',
        timestamp: Date.now(),
        step: (p?.step as string) ?? '3.4',
        title: (p?.title as string) ?? 'Tool output',
        description: '✓ Execution completed',
        status: 'completed',
        details: {
          output: p?.output,
        },
        substeps: [],
      };
    }

    if (kind === 'progress') {
      const p = safeParseJson(payload);
      if (p && typeof p === 'object') {
        return { ...p, type: 'progress', timestamp: Date.now() };
      }
      return null;
    }

    if (kind === 'error') {
      const p = safeParseJson(payload);
      return {
        type: 'error',
        timestamp: Date.now(),
        error_message: (p?.error_message as string) ?? payload,
        error_type: (p?.error_type as string) ?? 'replay_error',
        severity: 'recoverable',
      };
    }

    // Unknown replay kind — log and drop. Should not happen with the
    // current backend protocol but a debug-mode crash would be worse
    // than silent skip.
    console.warn('[wsManager] unknown replay kind:', kind, raw);
    return null;
  }

  // Live frame — pass through.
  return raw;
}


function safeParseJson(s: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(s);
    return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
