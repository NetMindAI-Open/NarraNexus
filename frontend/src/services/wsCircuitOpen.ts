/**
 * @file_name: wsCircuitOpen.ts
 * @description: Detect the WebSocket "agent circuit-breaker open" frame so the
 * app can surface a banner with a "Resume" action, symmetric with the
 * auth-expired path (wsAuthError.ts).
 *
 * Background: backend/routes/websocket.py sends a fresh-run rejection frame
 * `{type:'error', error_type:'agent_circuit_open', severity:'fatal',
 * cb_reason:'paused:auth'|'paused:quota'|'cooling'|'probing'}` when the real-time-layer
 * circuit-breaker (agent_framework/loop/circuit_breaker.py) is open for that
 * agent. Without this the user would just see a red chat bubble; the banner
 * gives them a one-click path to re-enable the agent once they've fixed the
 * underlying key/balance.
 *
 * Helper extracted so wsManager's run()/reconnect() handlers share it and the
 * logic is unit-testable without a real WebSocket.
 *
 * GitHub #117 follow-up: the banner used to be purely event-driven — once
 * shown it never re-checked reality, so a self-healed breaker (the backend's
 * half-open probe succeeding, or an owner confirming their key elsewhere)
 * left a stale "paused" banner up until the user manually dismissed or
 * retried. App.tsx now polls GET /api/agents/{id}/circuit-breaker (an
 * existing, already-wired endpoint — see agents_circuit_breaker.py) while
 * the banner is showing a "paused" reason (hooks/useCircuitBannerAutoClear),
 * and `shouldClearCircuitBanner` is the pure decision of whether that fresh
 * status means the banner is stale and should close itself.
 */

export interface MaybeCircuitOpenFrame {
  type?: unknown;
  error_type?: unknown;
  cb_reason?: unknown;
  [key: string]: unknown;
}

export interface AgentCircuitOpenDetail {
  agentId: string;
  reason: string; // "paused:auth" | "paused:quota" | "cooling" | "probing"
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

/** How often the "paused" banner re-checks GET .../circuit-breaker to see
 * whether the agent has already self-healed (half-open probe succeeded, or
 * the owner fixed the key from another tab/device). */
export const CIRCUIT_BREAKER_POLL_INTERVAL_MS = 30_000;

/** True when a freshly-fetched `cb_status` means the "paused" banner is
 * stale and should close itself: the breaker is back to `active` (the probe
 * succeeded / the owner fixed the key) or merely `cooling` (a short, self-
 * expiring backoff the banner copy already distinguishes). `probing` does
 * NOT clear it — a probe is in flight and its verdict is unknown; the user's
 * next message would still be refused, and closing the banner now only to
 * re-open it on a failed probe reads as flapping. */
export function shouldClearCircuitBanner(cbStatus: string): boolean {
  return cbStatus === 'active' || cbStatus === 'cooling';
}

/** What a showing banner should become given a freshly-fetched status:
 * `null` closes it (`shouldClearCircuitBanner`); a `paused` status maps to
 * `paused:<paused_reason>` — so a `probing` banner whose probe FAILED
 * escalates to the real pause, with the pause copy and the Resume action,
 * instead of promising "try again shortly" forever; `probing` (or a status
 * this build does not know) keeps the current reason. The caller updates
 * the banner only when the returned reason differs from the one shown. */
export function syncCircuitBannerReason(
  currentReason: string,
  cbStatus: string,
  pausedReason: string | null | undefined,
): string | null {
  if (shouldClearCircuitBanner(cbStatus)) return null;
  if (cbStatus === 'paused') return `paused:${pausedReason || 'unknown'}`;
  return currentReason;
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
