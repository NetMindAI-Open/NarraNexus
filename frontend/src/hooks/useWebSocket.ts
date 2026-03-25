/**
 * WebSocket hook — thin wrapper around wsManager
 *
 * Delegates connection management to the singleton wsManager service.
 * Provides React-friendly API with isLoading derived from chatStore.
 */

import { useCallback } from 'react';
import { wsManager } from '@/services/wsManager';
import { useChatStore } from '@/stores/chatStore';

interface UseAgentWebSocketOptions {
  onComplete?: (agentId: string) => void;
}

export function useAgentWebSocket(options: UseAgentWebSocketOptions = {}) {
  const isStreaming = useChatStore((s) => s.isStreaming);

  const run = useCallback(
    (agentId: string, userId: string, inputContent: string, agentName?: string) => {
      wsManager.run(agentId, userId, inputContent, {
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

  return {
    run,
    stop,
    close,
    isLoading: isStreaming,
  };
}
