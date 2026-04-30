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
import type { Attachment, RuntimeMessage } from '@/types';

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
    const wsUrl = `${getWsBaseUrl()}/ws/agent/run`;
    const ws = new WebSocket(wsUrl);

    const entry: ConnectionEntry = { ws, completed: false };
    this.connections.set(agentId, entry);

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
