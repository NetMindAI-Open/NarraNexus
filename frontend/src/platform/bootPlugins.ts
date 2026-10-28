/**
 * @file_name: bootPlugins.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: App-level wiring of the plugin loader: error reporting to the factory API, then `loadPlugins()`.
 *
 * `bootPlugins()` runs once, before React mounts — often while the user is not yet
 * authenticated. `GET /api/plugin-factory` then 401s and `loadPlugins()` returns `[]` with no
 * retry, so a user who logs in during the same SPA session (no full reload) never got their
 * plugins until a refresh (M-9, 2026-09-07). Fixed by subscribing to `useConfigStore` and
 * re-running `loadPlugins()` the moment `isLoggedIn` flips from `false` to `true` — safe because
 * `registerDeclaredUi`/`registerActivation` are idempotent for a plugin re-declaring its own
 * already-registered entries (see `loader.ts`'s "same owner" carve-out in its id-collision check).
 */
import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { useConfigStore } from '@/stores';
import { setErrorPoster } from './errorSink';
import { loadPlugins } from './loader';

/**
 * Returns the store-subscription's unsubscribe function — `main.tsx` calls this once via `void
 * bootPlugins()` and never needs it, but tests do (each test otherwise leaves its subscriber
 * live on the module-level `useConfigStore` singleton, double-firing the next test's).
 */
export async function bootPlugins(): Promise<() => void> {
  setErrorPoster((pluginId, body) =>
    fetch(`${getApiBaseUrl()}/api/plugin-factory/${encodeURIComponent(pluginId)}/errors`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(body),
    }),
  );
  // Subscribe BEFORE the first pass and read the login flag before it too: cloud inbound-token
  // entry (App.tsx `exchangeInboundToken` -> `login()`) can flip `isLoggedIn` while the first,
  // unauthenticated fetch is still in flight. That fetch then 401s, and a flag sampled only after
  // the await would never see the edge, so no second pass would run until a full reload.
  let wasLoggedIn = useConfigStore.getState().isLoggedIn;
  const unsubscribe = useConfigStore.subscribe((state) => {
    if (state.isLoggedIn && !wasLoggedIn) void loadPlugins();
    wasLoggedIn = state.isLoggedIn;
  });
  await loadPlugins();
  return unsubscribe;
}
