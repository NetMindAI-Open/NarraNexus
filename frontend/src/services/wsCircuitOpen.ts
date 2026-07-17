/**
 * @file_name: wsCircuitOpen.ts
 * @description: Detect the WebSocket "agent circuit-breaker open" frame so the
 * app can surface a banner with a "Resume" action, symmetric with the
 * auth-expired path (wsAuthError.ts).
 *
 * Background: backend/routes/websocket.py sends a fresh-run rejection frame
 * `{type:'error', error_type:'agent_circuit_open', severity:'fatal',
 * cb_reason:'paused:auth'|'paused:quota'|'cooling'}` when the real-time-layer
 * circuit-breaker (agent_framework/agent_circuit_breaker.py) is open for that
 * agent. Without this the user would just see a red chat bubble; the banner
 * gives them a one-click path to re-enable the agent once they've fixed the
 * underlying key/balance.
 *
 * Helper extracted so wsManager's run()/reconnect() handlers share it and the
 * logic is unit-testable without a real WebSocket.
 */

export interface MaybeCircuitOpenFrame {
  type?: unknown;
  error_type?: unknown;
  cb_reason?: unknown;
  [key: string]: unknown;
}

export interface AgentCircuitOpenDetail {
  agentId: string;
  reason: string; // "paused:auth" | "paused:quota" | "cooling"
}

/** True iff `message` is the backend's circuit-open rejection frame. */
export function isCircuitOpenMessage(message: unknown): boolean {
  if (!message || typeof message !== 'object') return false;
  const m = message as MaybeCircuitOpenFrame;
  return m.type === 'error' && m.error_type === 'agent_circuit_open';
}

/** Extract the reason string ("paused:auth" etc.), or "" if absent. */
export function circuitOpenReason(message: unknown): string {
  if (!isCircuitOpenMessage(message)) return '';
  const m = message as MaybeCircuitOpenFrame;
  return typeof m.cb_reason === 'string' ? m.cb_reason : '';
}

/**
 * Fire the app-wide `narranexus:agent-circuit-open` event carrying the
 * agent + reason. App.tsx listens and shows a banner with a Resume button.
 */
export function dispatchAgentCircuitOpen(detail: AgentCircuitOpenDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent<AgentCircuitOpenDetail>('narranexus:agent-circuit-open', { detail })
  );
}
