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
import { useArtifactStore, type ArtifactChangedEvent } from '@/stores/artifactStore';
import { useChatStore } from '@/stores/chatStore';
import { translateReconnectFrame } from '@/services/wsManager';
import { parseBackendTs } from '@/lib/backendTs';
import { isMonologueFrame } from '@/lib/monologueTier';
import type { Step, TurnEvent } from '@/types';

/**
 * Route one backend artifact_changed event: store upsert/remove via
 * applyEvent (monotonic-guarded, never throws), plus a toast when a heal
 * repointed the artifact at a different file — the pointer moved without
 * the user asking, so honesty requires telling them where it went (spec
 * artifact-events §5.3). Lives at module scope: it holds no hook state and
 * the reconnect path calls it too.
 */
function routeArtifactEvent(raw: Record<string, unknown>): void {
  const event = raw as unknown as ArtifactChangedEvent;
  useArtifactStore.getState().applyEvent(event);
  if (event.action === 'repointed' && event.artifact?.artifact_id) {
    useChatStore.getState().pushToast({
      kind: 'artifact-repointed',
      artifactId: event.artifact.artifact_id,
      title: event.artifact.title ?? event.artifact.artifact_id,
      oldPath: event.extra?.old ?? '?',
      newPath: event.extra?.new ?? '?',
      hashMatched: event.extra?.hash_matched === true,
      timestamp: Date.now(),
    });
  }
}

/**
 * Whether an error frame ends the socket's life, as opposed to being a
 * run-level event the run continues past.
 *
 * A named predicate rather than an inline condition because the retry ladder is
 * the thing it governs, and a retry loop is invisible in any test that only
 * folds frames: the reducer never sees `onclose`. This exact gap let the
 * breaker case reconnect forever while every reducer test stayed green — the
 * acceptance criterion says "the observation socket does not reconnect
 * endlessly", and nothing was checking it.
 *
 *   - Forbidden / NotFound / DBError: the server closes right after these.
 *   - agent_circuit_open: retrying is a loop against a refusal. The agent is
 *     not down, it is declining to run until a human fixes a key, a balance, or
 *     waits out a cooldown.
 *
 * Anything else is a run-level event (a failed tool call mid-run); settling on
 * those would end runs that are still going.
 */
export function isTerminalErrorFrame(errorType: unknown): boolean {
  return (
    errorType === 'Forbidden' ||
    errorType === 'NotFound' ||
    errorType === 'DBError' ||
    errorType === 'agent_circuit_open'
  );
}

export interface RunObservationSnapshot {
  /** connecting → live (frames flowing) → ended (terminal frame seen). */
  status: 'connecting' | 'live' | 'ended';
  /** Terminal state from run_ended / complete: completed | cancelled | failed. */
  endState: string | null;
  /** Process blocks in arrival order (thinking / tool_call / tool_output / plan). */
  events: TurnEvent[];
  /** Pipeline phases (step 0..3.4, build-context → run-agent), upserted by
   *  step id. Which of these render as rows is decided by the consumer's
   *  whitelist (processShared PHASE_STEP_IDS), not here. */
  steps: Step[];
  /** epoch ms the run started (from the run_reconnect metadata frame). */
  startedAt: number | null;
  /** Last fatal error surfaced by the run (display-only). */
  errorMessage: string | null;
  /**
   * Why the agent's breaker is open: "paused:auth" | "paused:quota" |
   * "cooling", else null.
   *
   * Kept rather than collapsed into `endState: 'failed'`, because the three
   * reasons ask the user for three different things — a new key, more balance,
   * or simply to wait. "Failed" tells them to do nothing in particular.
   */
  circuitReason: string | null;
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
  circuitReason: null,
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
    // parseBackendTs, not Date.parse (review #349 I1): cloud MySQL emits a
    // NAIVE-UTC string that bare Date.parse reads as local time — this
    // elapsed anchor would be off by the viewer's UTC offset the day a UI
    // starts rendering it.
    const startedMs = parseBackendTs(raw.started_at as string | null | undefined);
    return {
      ...INITIAL,
      status: 'live',
      startedAt: Number.isFinite(startedMs) ? startedMs : null,
    };
  }
  // An open breaker is TERMINAL, not "still loading". Left as a generic error
  // it wrote a message, never settled the snapshot, and the socket's onclose
  // then reconnected forever against an agent that is by definition refusing to
  // run — the room showing "couldn't load" while the client retried in a loop.
  // The private chat has had the honest path (a banner with Resume) for a long
  // time; this is the observation socket learning the same thing.
  if (t === 'error' && raw.error_type === 'agent_circuit_open') {
    return {
      ...snap,
      status: 'ended',
      endState: 'failed',
      circuitReason: (raw.cb_reason as string | null) ?? 'cooling',
      errorMessage: (raw.error_message as string | null) ?? snap.errorMessage,
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
    // The tier reaches this path too since 2026-08-30 (run_recorder tags the
    // segment, broadcaster carries the in-flight one, translateReconnectFrame
    // hands both back) — which is what makes the team member panel show
    // narration at all. It also means this merge can no longer be
    // unconditional: gluing a narration block onto a reasoning one would
    // label two tiers with a single flag. Same guard, same reason, as
    // chatStore's agent_thinking branch — its twin.
    const isMonologue = isMonologueFrame(
      content,
      translated.monologue as string | undefined,
    );
    if (last && last.type === 'thinking' && !!last.monologue === isMonologue) {
      events[events.length - 1] = { ...last, content: last.content + content };
    } else {
      events.push({
        type: 'thinking', id: nextId(), ts: Date.now(), content,
        monologue: isMonologue,
      });
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
        // Self-healing full pull (spec artifact-events §3.3): any events
        // missed while the socket was down are made irrelevant by re-pulling
        // the panel's list on every (re)connect. Fire-and-forget — a failed
        // refresh just leaves the panel one open/switch away from healing.
        const activeAgentId = useArtifactStore.getState().activeAgentId;
        if (activeAgentId) {
          void useArtifactStore
        .getState()
        .loadPinned(activeAgentId)
        .catch((e) => {
          // Loud, never fatal (same discipline as applyEvent's catch): a
          // failed full-pull means the panel may lag — worth a trace, not
          // an unhandled rejection on every reconnect blip (#334 I13).
          console.warn('artifact panel refresh on reconnect failed', e);
        });
        }
      };
      ws.onmessage = (event) => {
        try {
          const raw = JSON.parse(event.data) as Record<string, unknown>;
          if (raw.type === 'heartbeat') return;
          if (raw.type === 'artifact_changed') {
            // Artifact registry events feed the artifact store, not the run
            // timeline reducer — the tab panel is their UI, not the chat.
            routeArtifactEvent(raw);
            attempts = 0; // a live frame all the same — reset the backoff ladder
            return;
          }
          if (raw.type === 'error') {
            // Protocol-terminal errors (the server closes right after
            // these): stop the ladder AND settle the snapshot so the
            // panel stops pretending something is coming. Other error
            // frames are run-level events — dispatch, but do NOT reset
            // the ladder: an error frame is not progress.
            const et = raw.error_type as string | undefined;
            if (isTerminalErrorFrame(et)) {
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
