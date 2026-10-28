/**
 * @file_name: chunkReload.ts
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: Recovery primitives for stale lazy-chunk 404s after a deploy.
 *
 * The app code-splits routes with React.lazy (App.tsx). After a new build ships,
 * an already-open tab still references the OLD hashed chunk filenames; the next
 * navigation's dynamic import 404s and React.lazy throws, which
 * ChunkErrorBoundary catches and recovers from with a single reload.
 *
 * Deliberately NOT wired to the global `vite:preloadError` event: that fires for
 * EVERY dynamic import, including background prefetches (`void import(...)`) the
 * user never navigated to. Auto-reloading on those would blow away an unsaved
 * draft over a hover-triggered prefetch that failed on flaky wifi. Recovery is
 * therefore driven by the ErrorBoundary — it only runs when a chunk failure has
 * actually reached render, i.e. the user is already blocked.
 */

/** sessionStorage key for the one-reload-per-session guard (exported so tests
 *  reference the constant instead of duplicating the literal). */
export const RELOAD_GUARD_KEY = 'nx-chunk-reloaded';

/** sessionStorage, or null when the browser blocks it (privacy mode). */
function safeSessionStorage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Reload the page at most once per browsing session. A genuinely broken build
 * (the chunk truly 404s forever) must not wedge the tab in a reload loop, so the
 * guard is sticky for the session; a second failure shows the manual refresh
 * prompt instead. `reload`/`storage` are injected so this is a pure, testable
 * function. Returns true iff it triggered a reload.
 */
export function reloadOncePerSession(
  reload: () => void,
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = safeSessionStorage(),
): boolean {
  try {
    if (!storage || storage.getItem(RELOAD_GUARD_KEY)) return false;
    storage.setItem(RELOAD_GUARD_KEY, '1');
  } catch {
    return false; // storage unavailable mid-call — don't reload
  }
  reload();
  return true;
}

/** True when an error is a failed dynamic-import / chunk load (vs a real bug). */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '');
  return /dynamically imported module|Importing a module script failed|Loading chunk|ChunkLoadError/i.test(
    message,
  );
}
