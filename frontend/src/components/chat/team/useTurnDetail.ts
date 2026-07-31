/**
 * @file_name: useTurnDetail.ts
 * @date: 2026-07-31
 * @description: One finished turn's persisted process, fetched on demand.
 *
 * Shared by the roster's member detail and the transcript's per-message
 * disclosure — both live inside a room that polls every 3s, so a re-render
 * per tick must not become a request per tick. Extracted verbatim from
 * TeamRosterPanel's private hook when the transcript grew the same need.
 */

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { timelineToEvents } from '@/lib/segmentTurn';
import type { TurnEvent } from '@/types';

/**
 * A settled result for one turn. "Loading" is deliberately NOT a member of
 * this union: it is the absence of a settled state for the current key, so the
 * effect never has to write it — a synchronous setState inside an effect is a
 * cascading render (and an eslint error in this repo).
 *
 * 'empty' means the turn genuinely has no recorded process; 'error' means we
 * could not find out (network/server failure). Collapsing the two would tell
 * the user "no process record" about a turn that has one.
 */
export type TurnDetailState =
  | { key: string; kind: 'ready'; events: TurnEvent[] }
  | { key: string; kind: 'empty' }
  | { key: string; kind: 'error' };

export const isProcessEvent = (e: TurnEvent) =>
  e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output';

/**
 * Fetch a turn's event log once per `agent:event` key, first time `open` is
 * true. The key also decides the race — a response whose turn is no longer
 * the current one is dropped rather than painted over a newer turn's detail.
 */
export function useTurnDetail(
  agentId: string,
  eventId: string | null | undefined,
  open: boolean,
): TurnDetailState | null {
  const [state, setState] = useState<TurnDetailState | null>(null);
  const requestedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!open || !eventId) return;
    const key = `${agentId}:${eventId}`;
    // Already in flight or already held for this exact turn. (Deliberately not
    // an unmount-scoped `alive` flag: collapsing mid-flight would then strand
    // the row forever, since re-expanding hits this same cache line.)
    if (requestedRef.current === key) return;
    requestedRef.current = key;

    api
      .getEventLog(agentId, eventId)
      .then((r) => {
        if (requestedRef.current !== key) return;
        if (r.success && r.timeline && r.timeline.length > 0) {
          setState({ key, kind: 'ready', events: timelineToEvents(r.timeline) });
        } else {
          setState({ key, kind: 'empty' });
        }
      })
      .catch(() => {
        if (requestedRef.current !== key) return;
        // Unlike a settled 'ready'/'empty', a failure is retryable: clearing
        // the request marker means the next open (the effect re-runs when
        // `open` flips) issues a fresh fetch instead of hitting the cache line.
        requestedRef.current = null;
        setState({ key, kind: 'error' });
      });
  }, [open, agentId, eventId]);

  return state;
}
