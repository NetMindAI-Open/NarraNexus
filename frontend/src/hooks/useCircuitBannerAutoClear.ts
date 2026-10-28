/**
 * useCircuitBannerAutoClear — while a "paused" or "probing" circuit-breaker
 * banner is up, re-check the agent's real breaker status on an interval and
 * keep the banner in sync with it (`syncCircuitBannerReason`): close it once
 * the backend no longer reports the agent held (the half-open probe
 * succeeded, or the owner fixed the key from another tab/device), and turn a
 * "probing" banner into the real "paused:<reason>" one when the probe failed
 * — pause copy plus the Resume action, instead of "try again shortly".
 *
 * Extracted from App.tsx so the polling contract is testable with fake
 * timers instead of living inside the root component's effect soup:
 *   - checks immediately, then every CIRCUIT_BREAKER_POLL_INTERVAL_MS;
 *   - keyed on (agentId, reason, isLoggedIn) — NOT the banner object, which
 *     is a fresh reference on every rejected turn and would otherwise reset
 *     the interval on each retry, so the first poll never fired for exactly
 *     the users retrying most;
 *   - only while logged in: polling an authenticated endpoint after logout
 *     just produces 401s, and the banner itself is cleared on logout;
 *   - a failed fetch leaves the banner as-is (next tick retries) — a network
 *     blip must never dismiss a banner for an agent that is still paused.
 */
import { useEffect } from 'react';

import { api } from '@/lib/api';
import {
  CIRCUIT_BREAKER_POLL_INTERVAL_MS,
  syncCircuitBannerReason,
  type AgentCircuitOpenDetail,
} from '@/services/wsCircuitOpen';

export function useCircuitBannerAutoClear(
  circuitOpen: AgentCircuitOpenDetail | null,
  setCircuitOpen: (next: AgentCircuitOpenDetail | null) => void,
  isLoggedIn: boolean,
): void {
  const agentId = circuitOpen?.agentId ?? null;
  const reason = circuitOpen?.reason ?? '';

  useEffect(() => {
    if (!agentId) return;
    if (!isLoggedIn) {
      // Nothing to poll and nothing worth showing to a logged-out user.
      setCircuitOpen(null);
      return;
    }
    // Poll the reasons that outlive the next interaction: `paused*` and
    // `probing` (another turn holds the half-open probe — its outcome lands
    // seconds to minutes later and nothing else would ever close this banner).
    // `cooling` is short and clears on the next turn by itself.
    if (!reason.startsWith('paused') && reason !== 'probing') return;
    let cancelled = false;
    const pollStatus = async () => {
      try {
        const status = await api.getAgentCircuitBreaker(agentId);
        if (cancelled) return;
        const next = syncCircuitBannerReason(reason, status.cb_status, status.paused_reason);
        if (next === null) {
          setCircuitOpen(null);
        } else if (next !== reason) {
          // Only on a real change: `reason` keys this effect, so a
          // same-value update would restart the interval on every poll.
          setCircuitOpen({ agentId, reason: next });
        }
      } catch {
        // Best-effort re-check — leave the banner as-is; the next tick retries.
      }
    };
    void pollStatus();
    const id = window.setInterval(() => void pollStatus(), CIRCUIT_BREAKER_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // setCircuitOpen is a React state setter (stable identity).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, reason, isLoggedIn]);
}
