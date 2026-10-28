/**
 * @file_name: host.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The `HostAPI` a plugin bundle receives in `activate(host)` — the whole, deliberately small, host surface.
 *
 * A plugin sees: the shared framework libraries (so its ESM bundle can
 * mark them external and use the host's single copies), a per-registry
 * facade for each of the 17 registries it may contribute to, a JSON API
 * caller that carries the session auth, an i18n bundle adder scoped to
 * `plugin:<id>`, the error sink, and a disposable stack the loader unwinds
 * on deactivate. Nothing else: stores, components and internals are not
 * promised (Grafana's Angular lesson).
 *
 * `registries.*` used to hand the plugin the module-level `Registry`
 * instance itself (`host.registries.pages === PAGES`), which let a plugin
 * call `removeOwner()` / `freeze()` / register under someone else's owner —
 * the only thing actually stopping that was the separate `host.register()`
 * helper, which nobody was required to go through. Each registry is now
 * wrapped in a `PluginRegistryHandle`: owner is fixed to `pluginId` by the
 * closure, and `removeOwner` / `freeze` are simply not on the surface.
 */
import * as React from 'react';
import * as ReactDOM from 'react-dom';
import * as ReactRouter from 'react-router-dom';
import * as Zustand from 'zustand';
import i18next from 'i18next';
import * as Icons from 'lucide-react';

import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { addPluginBundle, pluginT } from './i18n';
import { reportUiError } from './errorSink';
import { REGISTRIES, RegistryConflictError, type Registry, type RegistryEntry } from './registries';

export const HOST_API_VERSION = 1;

export interface Disposable {
  dispose(): void;
}

/** The only door a plugin gets into a registry: `owner` is fixed to the plugin id, and there
 *  is no `removeOwner` / `freeze` — those stay host-only operations. */
export interface PluginRegistryHandle<T> {
  /** Add a new entry, or replace this same plugin's own previous entry under `id` (e.g. the
   *  loader's lazy gate). Registering over another owner's id throws `RegistryConflictError`. */
  register(id: string, value: T): Disposable;
  /** Same acceptance rule as `register`; the explicit name for "I know this id may already
   *  exist and it's mine". */
  replace(id: string, value: T): Disposable;
  /** Remove this plugin's own entry `id`; a no-op if `id` is not registered or not owned by it. */
  dispose(id: string): void;
  /** Read-only view of every entry currently in the registry (this plugin's and others'). */
  list(): RegistryEntry<T>[];
}

type RegistryValueOf<R> = R extends Registry<infer T> ? T : never;
type PluginRegistries = { [K in keyof typeof REGISTRIES]: PluginRegistryHandle<RegistryValueOf<(typeof REGISTRIES)[K]>> };

export interface HostAPI {
  version: string;
  api: { ui: number };
  pluginId: string;
  libs: {
    react: typeof React;
    reactDom: typeof ReactDOM;
    router: typeof ReactRouter;
    zustand: typeof Zustand;
    i18next: typeof i18next;
    icons: typeof Icons;
  };
  registries: PluginRegistries;
  /** Back-compat alias for `registries.<name>.register(id, value)` — kept only because the
   *  fixture bundle under `tests/plugins/hello_world/` (outside this repo's frontend/ slice)
   *  still calls the old three-arg form; new code should call the handle directly. */
  register<T>(registry: PluginRegistryHandle<T>, id: string, value: T): Disposable;
  http: {
    /** JSON request against the backend with the session's auth headers; relative paths only,
     *  and restricted to this plugin's own `/api/x/<id>` prefix. */
    request<T = unknown>(path: string, init?: RequestInit): Promise<T>;
    /** This plugin's own route prefix: `/api/x/<id>`. */
    prefix: string;
  };
  i18n: {
    addResourceBundle(lng: string, resources: Record<string, unknown>): void;
    t(key: string, options?: Record<string, unknown>): string;
  };
  log: { error(error: unknown, context?: string): void };
  subscriptions: Disposable[];
  dispose(): void;
}

declare global {
  interface Window {
    __narranexus_host__?: HostAPI['libs'] & { version: number };
  }
}

/** Publish the framework libraries once so plugin bundles resolve `external` imports to the host copies. */
export function exposeHostGlobals(): void {
  if (typeof window === 'undefined' || window.__narranexus_host__) return;
  window.__narranexus_host__ = {
    version: HOST_API_VERSION,
    react: React,
    reactDom: ReactDOM,
    router: ReactRouter,
    zustand: Zustand,
    i18next,
    icons: Icons,
  };
}

/** `requestJson` restricts a plugin to same-origin, `/api/x/<pluginId>`-prefixed calls.
 *
 * A bare `path.startsWith('/')` check (the previous rule) accepts `//evil.example/collect`:
 * browsers resolve a leading `//` as a protocol-relative URL, so the fetch actually lands on
 * `evil.example` carrying `Authorization` / `X-User-Id`. Resolving through `URL` and comparing
 * `origin` closes that; requiring the plugin's own prefix closes the (weaker, same-origin)
 * hole of one plugin calling another plugin's `/api/x/<other>` route or a bare `/api/...` one.
 */
async function requestJson<T>(pluginId: string, path: string, init?: RequestInit): Promise<T> {
  // In dev/cloud-web mode `getApiBaseUrl()` is intentionally empty (the Vite proxy / same
  // origin handles routing) — resolve relative URLs against the page origin in that case.
  const base = getApiBaseUrl() || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
  const baseOrigin = new URL(base).origin;
  let url: URL;
  try {
    url = new URL(path, base);
  } catch {
    throw new Error(`HostAPI.http.request: path must be relative (got "${path}")`);
  }
  if (url.origin !== baseOrigin) throw new Error(`HostAPI.http.request: path must be relative (got "${path}")`);
  const prefix = `/api/x/${pluginId}`;
  if (url.pathname !== prefix && !url.pathname.startsWith(`${prefix}/`)) {
    throw new Error(`HostAPI.http.request: path must start with "${prefix}" (got "${path}")`);
  }
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Wrap one shared `Registry<T>` into a facade fixed to `owner`. Each entry's `Disposable` is
 *  tracked BOTH under its own id (so `dispose(id)` can unregister exactly one entry — the raw
 *  `Registry` only offers `removeOwner`, which would drop every entry this plugin ever
 *  registered) and in the host-wide `subscriptions` stack `host.dispose()` unwinds; disposing
 *  one entry also removes it from that stack so a later `host.dispose()` does not double-fire it. */
function makeRegistryHandle<T>(registry: Registry<T>, owner: string, subscriptions: Disposable[]): PluginRegistryHandle<T> {
  const tracked = new Map<string, Disposable>();
  const ownsExisting = (id: string) => registry.has(id) && registry.ownerOf(id) === owner;
  const write = (id: string, value: T, forceReplace: boolean): Disposable => {
    const rawDispose = registry.register(id, value, { owner, replace: forceReplace || ownsExisting(id) });
    const entry: Disposable = {
      dispose: () => {
        rawDispose();
        tracked.delete(id);
        const idx = subscriptions.indexOf(entry);
        if (idx !== -1) subscriptions.splice(idx, 1);
      },
    };
    tracked.set(id, entry);
    subscriptions.push(entry);
    return entry;
  };
  return {
    register: (id, value) => write(id, value, false),
    replace: (id, value) => {
      if (registry.has(id) && !ownsExisting(id)) throw new RegistryConflictError(registry.kind, id, registry.ownerOf(id) ?? 'unknown');
      return write(id, value, true);
    },
    dispose: (id) => {
      tracked.get(id)?.dispose();
    },
    list: () => registry.list(),
  };
}

export function createHostApi(pluginId: string, hostVersion: string): HostAPI {
  const subscriptions: Disposable[] = [];
  // `Object.entries` widens each value to the union of all 16 registry value types, so the
  // per-key correlation `PluginRegistries` promises has to be restored with a cast — every
  // entry is still correctly wrapped, TypeScript just can't see the 1:1 mapping through the
  // entries()/fromEntries() round-trip.
  const registries = Object.fromEntries(
    Object.entries(REGISTRIES).map(([name, registry]) => [name, makeRegistryHandle(registry as Registry<unknown>, pluginId, subscriptions)]),
  ) as unknown as PluginRegistries;
  const host: HostAPI = {
    version: hostVersion,
    api: { ui: HOST_API_VERSION },
    pluginId,
    libs: { react: React, reactDom: ReactDOM, router: ReactRouter, zustand: Zustand, i18next, icons: Icons },
    registries,
    register<T>(handle: PluginRegistryHandle<T>, id: string, value: T): Disposable {
      return handle.register(id, value);
    },
    http: { request: (path, init) => requestJson(pluginId, path, init), prefix: `/api/x/${pluginId}` },
    i18n: {
      addResourceBundle: (lng, resources) => addPluginBundle(pluginId, lng, resources),
      t: (key, options) => pluginT(pluginId, key, options),
    },
    log: { error: (error, context) => reportUiError(error instanceof Error ? error : new Error(String(error)), { kind: 'render', source: pluginId, context }) },
    subscriptions,
    dispose() {
      for (const s of subscriptions.splice(0).reverse()) {
        try {
          s.dispose();
        } catch (e) {
          reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'render', source: pluginId, context: 'dispose' });
        }
      }
    },
  };
  return host;
}
