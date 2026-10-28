/**
 * @file_name: webAnalytics.ts
 * @date: 2026-08-12
 * @description: Consent-gated loader for Google Tag Manager (web surface only).
 *
 * GTM is NOT hardcoded in index.html. Loading it from code enforces gates an
 * inline <head> tag cannot:
 *
 *   1. Consent (single source of truth). Loads only after
 *      api.getAnalyticsOptOut() confirms the logged-in user has not opted out
 *      (Settings → Privacy, the "Product analytics" toggle whose copy now
 *      discloses this third-party analytics). Fail-closed on any error. Because
 *      opt-out is per-user, App.tsx calls this only once isLoggedIn. If a
 *      DIFFERENT, opted-out user later logs in in the same tab, a script tag
 *      can't be un-loaded, so we reload to shed it. Turning the toggle off
 *      mid-session reloads too (see PrivacySettings.toggleAnalytics, which also
 *      calls markWebAnalyticsConsentRevoked() to close the in-flight window).
 *   2. Desktop safety. isTauri() short-circuits — nothing loads inside the
 *      Tauri webview (csp: null + withGlobalTauri: true).
 *   3. Host + config gate. getWebAnalyticsConfig() returns a non-empty id only
 *      on the official production host (agent.narra.nexus); dev / CI / desktop /
 *      self-hosts send nothing. (An injected /config.js `gtmId` key or a
 *      VITE_GTM_ID build value can also supply it, but NO deploy injects one
 *      today — production's only live source is the host + a compiled-in
 *      constant. The immediate off-switch is the GTM console, not a redeploy.)
 *
 * Ownership: the GTM container is maintained by protagolabs; its tags/triggers
 * are configured in the GTM console, not in this repo.
 *
 * Related: first-party product events go through lib/productAnalytics.ts (a
 * separate, backend-DB path, no vendor). New client-side analytics files belong
 * in this lib/analytics/ subpackage.
 *
 * Clarity (session replay) is intentionally NOT loaded here: in a conversation
 * product its DOM-snapshot replay would capture chat content, contradicting the
 * localized "never collect conversation content" promise. GTM is event-only.
 *
 * Separate from the kernel analytics seam (src/narranexus/platform/analytics,
 * whose cloud PostHog sink stays NullSink) — see that module's docstring.
 */

import { isTauri } from '@/lib/tauri';
import { getWebAnalyticsConfig } from '@/lib/runtimeConfig';
import { api } from '@/lib/api';

let started = false;
// Set (within this page load) when the user revokes consent while an
// initWebAnalytics() call is mid-flight — guards the check-then-inject window.
let consentRevoked = false;

/** True once GTM has actually been injected in this page load. The reload-based
 *  consent enforcement (here and in PrivacySettings) keys on this, not on
 *  config: a mode where GTM never loaded must never pay a reload. */
export function isWebAnalyticsLoaded(): boolean {
  return started;
}

/** Mark consent revoked for the rest of this page load, so an in-flight
 *  initWebAnalytics() that already read opt-out=false does not still inject.
 *  Deliberately ONE-WAY: it is never cleared within a page load. PrivacySettings
 *  reloads after turning the toggle off ONLY when GTM had loaded, so on the rare
 *  path where GTM never loaded (opt-out lookup failed → fail-closed) the flag
 *  lingers until the next full page load. That only ever UNDER-loads (a
 *  re-enabled user waits until reload), never over-loads — the safe direction.
 *  If a "re-enable takes effect immediately" path is ever added, it must clear
 *  this. */
export function markWebAnalyticsConsentRevoked(): void {
  consentRevoked = true;
}

function injectGtm(id: string): void {
  const w = window as unknown as { dataLayer?: unknown[] };
  w.dataLayer = w.dataLayer || [];
  w.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
  const s = document.createElement('script');
  s.async = true;
  s.src = `https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(id)}`;
  document.head.appendChild(s);
}

/**
 * Load GTM for the current session iff: web build (not Tauri), an id is
 * configured (official host / deploy / VITE), and the logged-in user has not
 * opted out. Idempotent. If an opted-out user is seen after GTM already loaded
 * for a previous opted-in user in this tab, reloads to shed it. Never throws.
 */
export async function initWebAnalytics(): Promise<void> {
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  if (isTauri()) return;

  const { gtmId } = getWebAnalyticsConfig();
  if (!gtmId) return;

  let optedOut: boolean;
  try {
    optedOut = await api.getAnalyticsOptOut();
  } catch {
    return; // fail-closed: unknown consent loads nothing
  }

  // Consent may have been revoked during the await above (the toggle is on the
  // same page). Honour that over the now-stale lookup result.
  if (consentRevoked) return;

  if (optedOut) {
    // GTM already running for a previous (opted-in) user in this tab? A script
    // tag can't be un-loaded — reload so this opted-out user starts clean.
    if (started) window.location.reload();
    return;
  }

  if (started) return;
  started = true;
  injectGtm(gtmId);
}
