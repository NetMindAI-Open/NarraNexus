import { getAuthHeaders } from './authHeaders';
import { getApiBaseUrl } from '@/stores/runtimeStore';

export type ProductEventName =
  // First-run flow (WelcomePage). Replaced setup_* when /setup's single
  // provider screen became step 1 of the welcome flow — 2026-08-27.
  | 'welcome_entered'
  | 'welcome_skipped'
  | 'welcome_completed'
  | 'workspace_ready'
  | 'message_submitted'
  | 'reply_rendered'
  | 'subscribe_clicked'
  | 'checkout_opened';

export interface ProductEventDimensions {
  run_id?: string;
  agent_id?: string;
  trigger_source?: string;
  latency_ms?: number;
}

function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `evt_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

function sessionId(): string {
  if (typeof sessionStorage === 'undefined') return randomId();
  const key = 'narranexus:analytics-session-id';
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const created = randomId();
  sessionStorage.setItem(key, created);
  return created;
}

/** Fire-and-forget first-party event capture; never interrupts product flow. */
export function captureProductEvent(
  event: ProductEventName,
  dimensions: ProductEventDimensions = {},
): void {
  void fetch(`${getApiBaseUrl()}/api/analytics/events`, {
    method: 'POST',
    keepalive: true,
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({
      event,
      event_id: randomId(),
      session_id: sessionId(),
      ...dimensions,
    }),
  }).catch(() => undefined);
}
