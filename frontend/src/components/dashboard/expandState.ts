/**
 * @file_name: expandState.ts
 * @description: v2.1 — tiny hook wrapping sessionStorage for per-item
 * expansion. Keys are compound: `${agentId}:${scope}:${id?}`.
 *
 * Why sessionStorage (not localStorage): agent state changes by the minute;
 * yesterday's expanded "failed job" card may have been retried. Scoping to
 * tab session keeps UX responsive without showing stale expansions on a
 * fresh visit next day.
 */
import { useCallback, useSyncExternalStore } from 'react';

const STORAGE_KEY = 'dashboard:expanded';

function readAll(): Record<string, boolean> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

function writeAll(data: Record<string, boolean>): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    // Notify other hook instances in this tab.
    window.dispatchEvent(new Event('dashboard-expand-changed'));
  } catch {
    // sessionStorage unavailable (private mode, quota) — silently skip
  }
}

function subscribe(cb: () => void) {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener('dashboard-expand-changed', cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener('dashboard-expand-changed', cb);
    window.removeEventListener('storage', cb);
  };
}

export function useExpanded(key: string, defaultOpen = false) {
  const value = useSyncExternalStore(
    subscribe,
    () => (readAll()[key] ?? defaultOpen) as boolean,
    () => defaultOpen,
  );

  const toggle = useCallback(() => {
    const all = readAll();
    all[key] = !(all[key] ?? defaultOpen);
    writeAll(all);
  }, [key, defaultOpen]);

  const set = useCallback((v: boolean) => {
    const all = readAll();
    all[key] = v;
    writeAll(all);
  }, [key]);

  return { expanded: value, toggle, set } as const;
}

/**
 * v2.1.2 — true iff every provided banner key is marked dismissed in
 * sessionStorage. Returns `false` when the banner list is empty (there's
 * nothing to dismiss, so "all dismissed" is not a meaningful state).
 *
 * Reactive: re-renders on `dashboard-expand-changed` so dismissing the
 * last banner immediately dims the status rail without waiting for the
 * next polling tick.
 */
export function useAllBannersDismissed(
  bannerKeys: string[],
): boolean {
  return useSyncExternalStore(
    subscribe,
    () => {
      if (bannerKeys.length === 0) return false;
      const all = readAll();
      return bannerKeys.every((k) => all[k] === true);
    },
    () => false,
  );
}

/** Mirror of the banner sessionStorage key format used by AttentionBanners. */
export function bannerKey(agentId: string, kind: string, message: string): string {
  return `${agentId}:banner:${kind}:${encodeURIComponent(message)}`;
}
