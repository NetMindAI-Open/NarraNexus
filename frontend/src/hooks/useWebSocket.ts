/**
 * WebSocket hook — thin wrapper around wsManager
 *
 * Delegates connection management to the singleton wsManager service.
 * Provides React-friendly API with isLoading derived from chatStore.
 */

import { useCallback } from 'react';
import { wsManager } from '@/services/wsManager';
import { useChatStore } from '@/stores/chatStore';
import type { Attachment } from '@/types';

interface UseAgentWebSocketOptions {
  onComplete?: (agentId: string) => void;
}

export function useAgentWebSocket(options: UseAgentWebSocketOptions = {}) {
  const isStreaming = useChatStore((s) => s.isStreaming);

  const run = useCallback(
    (
      agentId: string,
      userId: string,
      inputContent: string,
      agentName?: string,
      attachments?: Attachment[],
      fastMode?: boolean,
    ) => {
      wsManager.run(agentId, userId, inputContent, {
        onComplete: options.onComplete,
        agentName,
        attachments,
        fastMode,
      });
    },
    [options.onComplete]
  );

  /**
   * Phase C: reconnect to an existing run by run_id.
   *
   * Used when the agent panel is mounted for an agent that has a
   * live BackgroundRun on the backend (signalled via
   * AgentInfo.active_run). The server replays the full event_stream
   * history then keeps the WS subscribed for live continuation.
   *
   * Idempotent — calling twice with the same agent closes the prior
   * connection first.
   */
  const reconnect = useCallback(
    (agentId: string, userId: string, runId: string, agentName?: string) => {
      wsManager.reconnect(agentId, userId, runId, {
        onComplete: options.onComplete,
        agentName,
      });
    },
    [options.onComplete]
  );

  const stop = useCallback((agentId: string) => {
    wsManager.stop(agentId);
  }, []);

  const close = useCallback((agentId: string) => {
    wsManager.close(agentId);
  }, []);

  // Fold a follow-up into the live run instead of starting a fresh one.
  // Returns true if it was actually sent (open, steerable run).
  const steer = useCallback((agentId: string, content: string, clientMsgId: string) => {
    return wsManager.steer(agentId, content, clientMsgId);
  }, []);

  // NB: no `isSteerable` wrapper is exposed here. ChatPanel gates the composer
  // on the store's reactive `currentSteerable` (a snapshot read wouldn't
  // re-render on run_started), and `steer()` itself re-checks liveness at send
  // time. `wsManager.isSteerable` stays public for non-React callers (#354
  // cloud delivery, team panel); a hook wrapper for a caller that doesn't exist
  // would be dead surface.
  return {
    run,
    reconnect,
    stop,
    close,
    steer,
    isLoading: isStreaming,
  };
}
