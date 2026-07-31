/**
 * @file_name: useRunObservation.ts
 * @author:
 * @date: 2026-07-31
 * @description: Observe ANY agent run, live, by run_id.
 *
 * Run observability is a platform property (see agent_runtime/
 * run_recorder.py): every run — chat, team room, Lark, job — persists
 * the same trace and is served by the same WS observe endpoint
 * (`/ws/agent/run?run_id=X`: replay + live continuation). This hook is
 * the frontend half of that contract: give it a run_id and it returns
 * a living snapshot — pipeline phases, process events (thinking / tool
 * calls / outputs), plan, elapsed anchor, terminal state — that any
 * surface can render with the shared process components. The team
 * roster's member detail uses it today; a dashboard "watch this
 * trigger run" view can use it unchanged tomorrow.
 *
 * Read-only by design: observing never starts, stops, or steers the
 * run (铁律 #14 — the platform must not be the interruption source).
 * Frames are translated by the SAME `translateReconnectFrame` the chat
 * reconnect path uses, so the two surfaces cannot drift.
 */
import { useEffect, useMemo, useReducer, useRef } from 'react';
import { getWsBaseUrl } from '@/stores/runtimeStore';
import { useConfigStore } from '@/stores';
import { translateReconnectFrame } from '@/services/wsManager';
import type { Step, TurnEvent } from '@/types';

export interface RunObservationSnapshot {
  /** connecting → live (frames flowing) → ended (terminal frame seen). */
  status: 'connecting' | 'live' | 'ended';
  /** Terminal state from run_ended / complete: completed | cancelled | failed. */
  endState: string | null;
  /** Process blocks in arrival order (thinking / tool_call / tool_output / plan). */
  events: TurnEvent[];
  /** Pre-loop pipeline phases (step 0..3), upserted by step id. */
  steps: Step[];
  /** epoch ms the run started (from the run_reconnect metadata frame). */
  startedAt: number | null;
  /** Last fatal error surfaced by the run (display-only). */
  errorMessage: string | null;
  /** Tool calls observed so far (derived; 0 while connecting). */
  opsCount: number;
}

const INITIAL: RunObservationSnapshot = {
  status: 'connecting',
  endState: null,
  events: [],
  steps: [],
  startedAt: null,
  errorMessage: null,
  opsCount: 0,
};

let idCounter = 0;
const nextId = () => `obs_${++idCounter}`;

type Action = { type: 'frame'; raw: Record<string, unknown> } | { type: 'reset' };

/**
 * Fold one observe-endpoint frame into the snapshot. Pure — exported
 * for tests. Mirrors chatStore.processMessage's event-building rules
 * (thinking merge, pending tool fold by tool_call_id, plan
 * replace-on-write) at observer scope: no session, no reply
 * extraction — the observed run's replies land on their own surface.
 */
export function applyObservationFrame(
  snap: RunObservationSnapshot,
  raw: Record<string, unknown>,
): RunObservationSnapshot {
  const t = raw.type as string | undefined;

  // Protocol metadata frames (absorbed by the translator) first.
  if (t === 'run_reconnect') {
    // A run_reconnect frame is ALWAYS the first frame of a full replay
    // (the endpoint replays event_stream from seq 0 on every attach),
    // so it restarts the snapshot from scratch. This makes the reducer
    // idempotent across observer-socket reconnects — without it, a
    // mid-run drop + backoff reopen would stack the whole trace twice
    // (tool_output rows append unconditionally; thinking would merge
    // doubled content).
    const startedRaw = raw.started_at as string | null | undefined;
    const startedMs = startedRaw ? Date.parse(startedRaw) : NaN;
    return {
      ...INITIAL,
      status: 'live',
      startedAt: Number.isFinite(startedMs) ? startedMs : null,
    };
  }
  if (t === 'run_ended') {
    return {
      ...snap,
      status: 'ended',
      endState: (raw.state as string) ?? 'completed',
      errorMessage: (raw.error_message as string | null) ?? snap.errorMessage,
    };
  }
  if (t === 'complete') {
    return {
      ...snap,
      status: 'ended',
      endState: (raw.state as string) ?? 'completed',
    };
  }
  if (t === 'cancelled') {
    return { ...snap, status: 'ended', endState: 'cancelled' };
  }

  const translated = translateReconnectFrame(raw) as Record<string, unknown> | null;
  if (translated === null) return snap;
  const tt = translated.type as string | undefined;

  if (tt === 'agent_thinking') {
    const content = (translated.thinking_content as string) ?? '';
    if (!content) return snap;
    const events = [...snap.events];
    const last = events[events.length - 1];
    if (last && last.type === 'thinking') {
      events[events.length - 1] = { ...last, content: last.content + content };
    } else {
      events.push({ type: 'thinking', id: nextId(), ts: Date.now(), content });
    }
    return { ...snap, status: 'live', events };
  }

  if (tt === 'agent_plan') {
    const steps = (translated.steps as Array<{ step: string; status: string }>) ?? [];
    const events = [...snap.events];
    const existing = events.findIndex((ev) => ev.type === 'plan');
    const block: TurnEvent = {
      type: 'plan',
      id: existing >= 0 ? events[existing].id : nextId(),
      ts: Date.now(),
      steps,
      note: translated.note as string | undefined,
    };
    if (existing >= 0) events[existing] = block;
    else events.push(block);
    return { ...snap, status: 'live', events };
  }

  if (tt === 'progress') {
    const details = (translated.details as Record<string, unknown>) ?? {};
    const toolName = (details.tool_name as string) || '';
    const args = details.arguments as Record<string, unknown> | undefined;
    const rawOutput = details.output;

    if (toolName && args !== undefined) {
      const callId = details.tool_call_id as string | undefined;
      const events = [...snap.events];
      // Pending fold: the name-first entry is replaced in place by the
      // completed call (same id → the row updates instead of remounting).
      const pendingIdx = callId
        ? events.findIndex((e) => e.type === 'tool_call' && e.tool_call_id === callId)
        : -1;
      const call: TurnEvent = {
        type: 'tool_call',
        id: pendingIdx >= 0 ? events[pendingIdx].id : nextId(),
        ts: Date.now(),
        tool_name: toolName,
        tool_input: args,
        tool_call_id: callId,
        pending: !!details.pending,
      };
      if (pendingIdx >= 0) events[pendingIdx] = call;
      else events.push(call);
      return { ...snap, status: 'live', events };
    }

    if (rawOutput !== undefined && rawOutput !== null) {
      const output = typeof rawOutput === 'string' ? rawOutput : JSON.stringify(rawOutput);
      const events: TurnEvent[] = [
        ...snap.events,
        {
          type: 'tool_output',
          id: nextId(),
          ts: Date.now(),
          tool_name: toolName,
          tool_call_id: details.tool_call_id as string | undefined,
          output,
        },
      ];
      return { ...snap, status: 'live', events };
    }

    // Pipeline phase — upsert by step id (same replace-on-write the
    // chat store applies to currentSteps).
    const stepId = (translated.step as string) ?? '';
    if (!stepId) return snap;
    const step: Step = {
      id: stepId,
      step: stepId,
      title: (translated.title as string) ?? '',
      description: (translated.description as string) ?? '',
      status: ((translated.status as Step['status']) ?? 'running'),
      substeps: (translated.substeps as string[]) ?? [],
      details,
      timestamp: Date.now(),
    };
    const steps = [...snap.steps];
    const idx = steps.findIndex((s) => s.step === stepId);
    if (idx >= 0) steps[idx] = step;
    else steps.push(step);
    return { ...snap, status: 'live', steps };
  }

  if (tt === 'error') {
    const severity = (translated.severity as string) ?? 'fatal';
    if (severity === 'fatal') {
      return {
        ...snap,
        errorMessage: (translated.error_message as string) ?? 'Unknown error',
      };
    }
    return snap;
  }

  // Frames the observer has no surface for (agent_response deltas — the
  // reply lands on the observed run's own surface; heartbeat; stopping).
  return snap;
}

function reducer(snap: RunObservationSnapshot, action: Action): RunObservationSnapshot {
  if (action.type === 'reset') return INITIAL;
  return applyObservationFrame(snap, action.raw);
}

/** Reconnect backoff for the observer socket: gentle and capped — the
 *  run outlives any number of observer sockets. */
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 10000;

/**
 * Observe one run. Pass ``enabled: false`` (or a null runId) to keep
 * the hook mounted but idle — the socket opens only while enabled, so
 * a collapsed roster row costs nothing.
 */
export function useRunObservation(
  runId: string | null | undefined,
  { enabled = true }: { enabled?: boolean } = {},
): RunObservationSnapshot {
  const [snap, dispatch] = useReducer(reducer, INITIAL);
  const userId = useConfigStore((s) => s.userId);
  // The latest terminal status, readable from socket callbacks without
  // re-running the connection effect on every frame.
  const endedRef = useRef(false);
  useEffect(() => {
    endedRef.current = snap.status === 'ended';
  }, [snap.status]);
  // Set SYNCHRONOUSLY when the server says this run can never be
  // streamed by this client (Forbidden / NotFound / DBError). endedRef
  // lags a render behind (it follows reducer state through an effect),
  // and the server closes the socket right after the error frame — an
  // onclose racing that effect would still schedule a retry. A fatal
  // protocol answer is not a network blip; retrying has zero upside.
  const fatalRef = useRef(false);

  useEffect(() => {
    dispatch({ type: 'reset' });
    fatalRef.current = false;
    if (!runId || !enabled || !userId) return;

    let ws: WebSocket | null = null;
    let retryTimer: number | null = null;
    let attempts = 0;
    let disposed = false;

    const open = () => {
      if (disposed) return;
      try {
        const url = `${getWsBaseUrl()}/ws/agent/run?x_user_id=${encodeURIComponent(userId)}`;
        ws = new WebSocket(url);
      } catch {
        // No WebSocket in this environment — the panel degrades to its
        // "starting up" fallback instead of crashing the roster.
        return;
      }
      ws.onopen = () => {
        const token = useConfigStore.getState().token;
        ws?.send(JSON.stringify({
          run_id: runId,
          user_id: userId,
          token: token || undefined,
        }));
      };
      ws.onmessage = (event) => {
        try {
          const raw = JSON.parse(event.data) as Record<string, unknown>;
          if (raw.type === 'heartbeat') return;
          if (raw.type === 'error') {
            // Protocol-terminal errors (the server closes right after
            // these): stop the ladder AND settle the snapshot so the
            // panel stops pretending something is coming. Other error
            // frames are run-level events — dispatch, but do NOT reset
            // the ladder: an error frame is not progress.
            const et = raw.error_type as string | undefined;
            if (et === 'Forbidden' || et === 'NotFound' || et === 'DBError') {
              fatalRef.current = true;
              dispatch({ type: 'frame', raw });
              dispatch({
                type: 'frame',
                raw: {
                  type: 'run_ended',
                  state: 'failed',
                  error_message: raw.error_message,
                },
              });
              return;
            }
            dispatch({ type: 'frame', raw });
            return;
          }
          attempts = 0; // progress frames flowing — reset the backoff ladder
          dispatch({ type: 'frame', raw });
        } catch {
          // one bad frame must not kill the observer
        }
      };
      ws.onclose = () => {
        if (disposed || endedRef.current || fatalRef.current) return;
        // The run may still be alive (network blip / backend deploy) —
        // keep observing with capped backoff. run_ended/complete flip
        // endedRef and stop the ladder.
        const delay = Math.min(RETRY_BASE_MS * 2 ** attempts, RETRY_MAX_MS);
        attempts += 1;
        retryTimer = window.setTimeout(open, delay);
      };
    };
    open();

    return () => {
      disposed = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      ws?.close();
    };
  }, [runId, enabled, userId]);

  // Derived, memoised: ops = tool calls observed so far.
  const opsCount = useMemo(
    () => snap.events.filter((e) => e.type === 'tool_call').length,
    [snap.events],
  );

  return useMemo(() => ({ ...snap, opsCount }), [snap, opsCount]);
}
