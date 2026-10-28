/**
 * @file_name: sessionGuard.ts
 * @description: Second opinion before a forced logout.
 *
 * Ending a session is the most destructive thing this frontend does to
 * itself: `configStore.logout()` clears identity, `ProtectedRoute` swaps the
 * whole tree for /login, every mounted panel unmounts and the WS drops. On
 * 2026-08-02 a single 401 was enough to trigger it, repeatedly, mid-demo —
 * users read the teardown as "the page reloaded and everything got
 * confusing" (there is no actual page reload; nothing here touches
 * window.location).
 *
 * So a session-death 401 is now treated as a *suspicion*, not a verdict.
 * We ask `GET /api/auth/session` — the cheapest possible "am I still
 * logged in?" — and only tear down when it agrees. Anything else (200,
 * 5xx, network error) leaves the session alone: a false negative costs the
 * user one failed request, a false positive costs them everything they had
 * on screen.
 *
 * Upstream: [[api.ts]] (REST 401s), [[wsAuthError.ts]] (WS AuthError frames).
 * Downstream: [[App.tsx]] listens for `narranexus:auth-expired`.
 */
import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from './authHeaders';

export interface SessionDeathSuspicion {
  /** The endpoint whose 401 raised the suspicion (for diagnostics). */
  endpoint: string;
  /** The backend's auth code, or null when the signal came from the WS. */
  code: string | null;
}

/**
 * In-flight probe. A page mount fires a dozen requests at once, so a dead
 * JWT arrives as a *burst* of 401s — without this, each one probed and each
 * one dispatched, giving N logouts and N banners for one dead session.
 */
let inFlight: Promise<void> | null = null;
/** Latched once we've confirmed death, so late 401s from the same burst
 *  don't re-probe an already-torn-down session. Cleared by resetSessionGuard. */
let confirmed = false;

/** Test seam — also called on a fresh login so a new session starts clean. */
export function resetSessionGuard(): void {
  inFlight = null;
  confirmed = false;
}

async function probe(): Promise<'dead' | 'alive' | 'unknown'> {
  try {
    const res = await fetch(`${getApiBaseUrl()}/api/auth/session`, {
      headers: getAuthHeaders(),
    });
    if (res.status === 401) return 'dead';
    if (res.ok) return 'alive';
    // 5xx / 402 / anything else: the backend is unhappy for reasons that
    // are not "this user is logged out". Not our call to make.
    return 'unknown';
  } catch {
    // Offline, DNS, backend restarting. Killing the session on a network
    // blip is the exact nuclear behaviour this module exists to prevent.
    return 'unknown';
  }
}

/**
 * Verify a suspected-dead session and, if confirmed, fire
 * `narranexus:auth-expired` (once) with the triggering endpoint + code so
 * the next incident is diagnosable from the client side too.
 *
 * Resolves in all cases — callers do not branch on the outcome; they throw
 * their own ApiError regardless.
 */
export async function confirmSessionDeath(
  suspicion: SessionDeathSuspicion,
): Promise<void> {
  if (confirmed) return;
  // No credential attached → there is no session to lose. Anonymous
  // probes and pre-login requests must never reach the teardown path.
  const headers = getAuthHeaders();
  if (!headers.Authorization && !headers['X-User-Id']) return;

  if (!inFlight) {
    inFlight = (async () => {
      const verdict = await probe();
      if (verdict !== 'dead') {
        console.warn(
          `[session-guard] 401 on ${suspicion.endpoint} (code=${suspicion.code}) ` +
            `but the session probe says ${verdict} — not logging out`,
        );
        return;
      }
      confirmed = true;
      window.dispatchEvent(
        new CustomEvent('narranexus:auth-expired', { detail: suspicion }),
      );
    })().finally(() => {
      inFlight = null;
    });
  }
  await inFlight;
}
