/**
 * @file_name: tokenInbound.ts
 * @description: Power login-state pass-through (scenario A). When the page
 * is opened with ?token=<NetMind loginToken> (e.g. a link from netmind.ai
 * or Arena), take the token, strip it from the URL immediately (avoid
 * leaking it into history), and exchange it for our session. `source` is
 * read alongside and forwarded for downstream provisioning (Phase 2).
 */
import { api } from '@/lib/api';

export interface InboundResult { handled: boolean; token?: string; source?: string }

/** sessionStorage key the Arena landing flow reads to know we entered via Arena. */
export const ENTRY_SOURCE_KEY = 'nx-entry-source';

/** Parse + strip ?token=/?source= from the URL. Returns what was found. */
export function takeInboundToken(loc: { search: string; pathname: string; hash: string }): InboundResult {
  const params = new URLSearchParams(loc.search);
  const token = params.get('token');
  const source = params.get('source') || undefined;
  if (!token) return { handled: false, source };
  params.delete('token');
  const rest = params.toString();
  const newUrl = loc.pathname + (rest ? `?${rest}` : '') + loc.hash;
  window.history.replaceState(null, '', newUrl);
  return { handled: true, token, source };
}

let _inbound: InboundResult | null = null;

/**
 * Capture inbound ?token/?source from the TRUE entry URL, synchronously, at
 * startup — BEFORE React renders and before any <Navigate> redirect can
 * rewrite the URL. Stashes `source` into sessionStorage so the Arena landing
 * flow survives the logged-out → /login → logged-in redirect chain.
 *
 * Must be called once from main.tsx, before createRoot().render(). Reading the
 * URL in an App useEffect is too late: for an unauthenticated Arena entry,
 * RootRedirect synchronously renders <Navigate to="/login">, whose navigation
 * effect (a descendant) fires before App's mount effect and drops ?source.
 * Idempotent — repeated calls return the first captured result.
 */
export function captureInboundEntry(): InboundResult {
  if (_inbound) return _inbound;
  _inbound = takeInboundToken(window.location);
  if (_inbound.source) {
    try {
      sessionStorage.setItem(ENTRY_SOURCE_KEY, _inbound.source);
    } catch {
      /* sessionStorage may be unavailable */
    }
  }
  return _inbound;
}

/** The inbound result captured at startup by captureInboundEntry(). */
export function getInboundEntry(): InboundResult {
  return _inbound ?? { handled: false };
}

/** Exchange an inbound NetMind token for our session. Returns the response. */
export async function exchangeInboundToken(token: string, source?: string) {
  return api.netmindLogin(token, source);
}
