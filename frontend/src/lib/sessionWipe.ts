/**
 * @file_name: sessionWipe.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: The one authoritative "leave this session" wipe — reset the
 * stores AND hard-remove every persisted key that could carry session state.
 *
 * Extracted from Sidebar (2026-08-27) when the first-run flow got its own
 * "log out" affordance: a second, hand-rolled half-logout is how cloud data
 * bled into a later local session in the first place. Callers follow this with
 * a FULL document load (`window.location.href = …`), never a soft navigate —
 * soft navigation keeps the React tree, closure-captured store snapshots,
 * in-flight fetches and module caches from the old session alive.
 *
 * Deliberately aggressive: Zustand's persist middleware may not have flushed by
 * the time the reload happens, so the localStorage removals — not the store
 * resets — are what guarantee factory defaults on the next load.
 *
 * Keys wiped:
 *   - narra-nexus-config      → configStore (userId, token, agents, …)
 *   - narranexus-runtime      → runtimeStore (mode, cloudApiUrl, …)
 *   - lastSeenAwarenessTime:* → written directly by configStore, not covered
 *                               by any store's clearAll
 */

import { useConfigStore, useChatStore, usePreloadStore } from '@/stores';
import { clearOnboardingGateCache } from './onboardingGate';

export function wipeAllSessionData(): void {
  // 1. In-memory store state, so the UI reacts immediately (and persist gets a
  //    chance to sync — best-effort, not relied on).
  useConfigStore.getState().logout();
  useChatStore.getState().clearAll();
  usePreloadStore.getState().clearAll();
  // Module-scope, so it survives a store reset: the next user must not inherit
  // this one's "already onboarded" answer.
  clearOnboardingGateCache();

  // 2. The authoritative clear.
  try {
    localStorage.removeItem('narra-nexus-config');
    localStorage.removeItem('narranexus-runtime');

    const auxKeys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('lastSeenAwarenessTime:')) auxKeys.push(k);
    }
    auxKeys.forEach((k) => localStorage.removeItem(k));
  } catch {
    // Safari private mode / other storage exceptions — ignore.
  }
}
