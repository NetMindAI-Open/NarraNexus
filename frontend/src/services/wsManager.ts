/**
 * WebSocket connection manager — singleton service
 *
 * Manages multiple concurrent WebSocket connections (one per agent).
 * Decoupled from React component lifecycle so connections persist across agent switches.
 */

import { useChatStore } from '@/stores/chatStore';
import type { RuntimeMessage } from '@/types';

interface ConnectionEntry {
  ws: WebSocket;
  completed: boolean;
}

type OnCompleteCallback = (agentId: string) => void;

class WebSocketManager {
  private connections = new Map<string, ConnectionEntry>();
  private onCompleteCallbacks = new Map<string, OnCompleteCallback>();

  /** Start a new agent run via WebSocket */
  run(
    agentId: string,
    userId: string,
    inputContent: string,
    options?: {
      onComplete?: OnCompleteCallback;
      agentName?: string;
    },
  ): void {
    // Close existing connection for this agent if any
    this.close(agentId);

    if (options?.onComplete) {
      this.onCompleteCallbacks.set(agentId, options.onComplete);
    }

    const agentName = options?.agentName;
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/agent/run`;
    const ws = new WebSocket(wsUrl);

    const entry: ConnectionEntry = { ws, completed: false };
    this.connections.set(agentId, entry);

    const store = useChatStore.getState;

    ws.onopen = () => {
      ws.send(JSON.stringify({
        agent_id: agentId,
        user_id: userId,
        input_content: inputContent,
        working_source: 'chat',
      }));
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as RuntimeMessage;

        // Skip heartbeats
        if (message.type === 'heartbeat') return;

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

      if (!entry.completed) {
        // Unexpected disconnect — stop streaming with error state
        console.warn(`[wsManager] WebSocket closed unexpectedly for ${agentId}`);
        store().stopStreaming(agentId, agentName);
      }
    };
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
}

export const wsManager = new WebSocketManager();
