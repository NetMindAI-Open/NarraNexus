/**
 * M-9: `bootPlugins()` runs once, before React mounts — often while the user is not yet
 * authenticated. `GET /api/plugin-factory` then 401s and `loadPlugins()` returns `[]` with no
 * retry, so a user who logs in during the same SPA session (no full reload) never gets their
 * plugins until they refresh. `loadPlugins()` must re-run the moment auth flips from absent to
 * present.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../loader', () => ({ loadPlugins: vi.fn().mockResolvedValue([]) }));
vi.mock('../errorSink', () => ({ setErrorPoster: vi.fn() }));

import { bootPlugins } from '../bootPlugins';
import { loadPlugins } from '../loader';
import { useConfigStore } from '@/stores';

const initialState = useConfigStore.getState();
let unsubscribe: (() => void) | undefined;
afterEach(() => {
  unsubscribe?.();
  unsubscribe = undefined;
  useConfigStore.setState(initialState, true);
  vi.clearAllMocks();
});

describe('bootPlugins (M-9)', () => {
  it('calls loadPlugins() once on boot', async () => {
    unsubscribe = await bootPlugins();
    expect(loadPlugins).toHaveBeenCalledTimes(1);
  });

  it('re-runs loadPlugins() when auth flips from absent to present', async () => {
    unsubscribe = await bootPlugins();
    expect(loadPlugins).toHaveBeenCalledTimes(1);
    useConfigStore.getState().login('u1', 'token');
    expect(loadPlugins).toHaveBeenCalledTimes(2);
  });

  it('does not re-run loadPlugins() on a store update that is not an auth transition', async () => {
    unsubscribe = await bootPlugins();
    expect(loadPlugins).toHaveBeenCalledTimes(1);
    useConfigStore.getState().setAgentId('a1');
    expect(loadPlugins).toHaveBeenCalledTimes(1);
  });

  it('does not re-run loadPlugins() while already logged in (no absent-to-present edge)', async () => {
    useConfigStore.getState().login('u1', 'token');
    unsubscribe = await bootPlugins();
    expect(loadPlugins).toHaveBeenCalledTimes(1);
    useConfigStore.getState().login('u1', 'token-2');
    expect(loadPlugins).toHaveBeenCalledTimes(1);
  });

  it('a login that lands while the FIRST pass is still in flight triggers a second pass', async () => {
    // Cloud inbound-token entry (App.tsx exchangeInboundToken -> login()) flips isLoggedIn while the
    // unauthenticated first fetch is pending: that fetch 401s, and with the flag read only after
    // the await no second pass ever ran.
    let finishFirst!: (rows: []) => void;
    vi.mocked(loadPlugins).mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }));
    const booting = bootPlugins();
    useConfigStore.getState().login('u1', 'token');
    expect(loadPlugins).toHaveBeenCalledTimes(2);
    finishFirst([]);
    unsubscribe = await booting;
    expect(loadPlugins).toHaveBeenCalledTimes(2); // the settled first pass does not fire a third
  });

  it('a normal boot-then-login still runs exactly two passes', async () => {
    unsubscribe = await bootPlugins();
    useConfigStore.getState().login('u1', 'token');
    useConfigStore.getState().login('u1', 'token-2');
    expect(loadPlugins).toHaveBeenCalledTimes(2);
  });
});
