/**
 * @file_name: loader.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Loads user plugins into the shell: declarative metadata first (gates in the registries), code on activation, with SRI.
 *
 * `loadPlugins()` asks the factory API which plugins are enabled and
 * loaded, registers a lazy gate for every page/panel/command their manifest
 * declares, and arms activation. Activation fetches the plugin's `plugin.js`
 * (desktop: `plugin://<id>/…`; web: the factory's asset route), verifies
 * the manifest's `integrity` (sha256 SRI) when present, imports it as an
 * ESM module from a blob URL, and calls `plugin.activate(host)`. The blob
 * URL is recorded for error attribution. Nothing here throws to the
 * caller: a broken plugin costs one error report, never the shell — an
 * illegal declaration (an `/app` page claiming a non-`protected` guard) is
 * rejected and reported at registration time rather than reaching the
 * registry and blowing up `pageRouteElements` at render time.
 *
 * `disableBuiltinUi` does two things, not one: it purges every entry a
 * disabled owner already has (`removeOwner` on every registry in
 * `REGISTRIES`), and it blacklists the owner (`disableOwner`) so a
 * registration that has not happened yet — a lazily-loaded settings
 * section, a channel row registered the first time its chunk mounts —
 * can never land either. Without the blacklist half, a builtin disabled
 * at boot (before its lazy chunk ever imported) would reappear the moment
 * a user opened that surface.
 */

import { isTauri } from '@/lib/tauri';
import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { fireActivation, registerActivation } from './activation';
import { attributeChunkUrl, reportUiError } from './errorSink';
import { makeActionGate } from './actionGate';
import { makeArtifactKindGate, makePageGate, makePanelGate, makeRendererGate, makeSlotGate, makeTimelineGate } from './gates';
import { createHostApi, exposeHostGlobals, type HostAPI } from './host';
import {
  AGENT_CARD_BADGES,
  ARTIFACT_KINDS,
  CHAT_HEADER_ACTIONS,
  COMMANDS,
  COMPOSER_EXTENSIONS,
  CONVERSATION_KINDS,
  MESSAGE_ACTIONS,
  MESSAGE_RENDERERS,
  PAGES,
  PANELS,
  REGISTRIES,
  SIDEBAR_SECTIONS,
  TIMELINE_EVENTS,
  TOP_BAR_ITEMS,
  disableOwner,
  type CommandDef,
  type Registry,
  type SlotActionDef,
  type SlotComponentDef,
} from './registries';

export interface FactoryPluginRow {
  id: string;
  version: string;
  enabled: boolean;
  loaded: boolean;
  state: string;
  /** How the installer put the plugin's files on disk: "copy" (a downloaded tarball/repo,
   *  written into the plugin store) vs "link" (a symlink to a local dev checkout). Backs the
   *  SRI-required-for-copy-installs check in `activatePlugin` (architecture E3(a)). */
  mode?: 'copy' | 'link';
  frontend: null | {
    entry: string;
    locales?: string;
    integrity?: string;
    ui?: {
      pages?: { id: string; path: string; layout?: 'app' | 'top'; guard?: 'protected' | 'public' | 'open' }[];
      panels?: { id: string; label?: string }[];
      commands?: { id: string; label: string; hint?: string }[];
      themes?: string[];
      /** Kinds `when: conversationKind:<k>` may name; registered up front. */
      conversationKinds?: { id: string; label?: string }[];
      /** A gate renders until the plugin registers the real renderer under the same id. */
      messageRenderers?: { id: string; role?: 'user' | 'assistant'; contentPrefix?: string }[];
      timelineEvents?: { id: string; type: string }[];
      /** Artifact kinds the plugin renders; a gate descriptor holds the id until the plugin registers the real one. */
      artifactKinds?: { id: string; label?: string; downloadExt?: string }[];
      /** Slot-point entries declared up front (component slots mount a silent gate; action slots a labelled one). */
      slots?: { id: string; point: SlotPoint; label?: string; when?: string[]; order?: number }[];
    };
  };
  activation_events?: string[];
}

export interface FactoryBuiltinRow {
  id: string;
  enabled: boolean;
  /** Left out by the running distribution (its routes/workers never exist): unlike a user-disabled row, `protected` does not shield it. */
  excluded: boolean;
  protected: boolean;
}

/**
 * Remove every shell registration owned by a disabled builtin plugin (its pages,
 * sidebar rows, panels, commands…) and blacklist the owner so a registration
 * that has not happened yet (a lazily-registered channel row, a settings
 * section pulled in only when its chunk loads) can never land either.
 * `platform/builtin.ts` tags feature-level builtins with their plugin id as
 * owner, so the whole UI row goes with one call.
 */
export function disableBuiltinUi(pluginId: string): string[] {
  disableOwner(pluginId);
  const removed: string[] = [];
  for (const reg of SHELL_REGISTRIES) removed.push(...reg.removeOwner(pluginId).map((id) => `${reg.kind}:${id}`));
  return removed;
}

export type SlotPoint = 'chatHeaderActions' | 'composerExtensions' | 'messageActions' | 'sidebarSections' | 'agentCardBadges' | 'topBarItems';

const COMPONENT_SLOTS: Record<string, Registry<SlotComponentDef>> = {
  composerExtensions: COMPOSER_EXTENSIONS,
  sidebarSections: SIDEBAR_SECTIONS,
  agentCardBadges: AGENT_CARD_BADGES,
  topBarItems: TOP_BAR_ITEMS,
};
const ACTION_SLOTS: Record<string, Registry<SlotActionDef>> = {
  chatHeaderActions: CHAT_HEADER_ACTIONS,
  messageActions: MESSAGE_ACTIONS,
};

/** Every shell registry a disabled builtin's UI row is removed from (`disableBuiltinUi`) —
 *  every entry of `REGISTRIES` (the one registry-name table; see `registries/index.ts`). */
const SHELL_REGISTRIES = Object.values(REGISTRIES);

export interface PluginModule {
  plugin?: { activate(host: HostAPI): void | Promise<void>; deactivate?(host: HostAPI): void | Promise<void> };
  activate?(host: HostAPI): void | Promise<void>;
}

export interface LoaderDeps {
  fetchImpl?: typeof fetch;
  importImpl?: (url: string) => Promise<PluginModule>;
  /** SHA-256 over the bundle bytes; defaults to WebCrypto (tests inject node:crypto). */
  digestImpl?: (bytes: ArrayBuffer) => Promise<ArrayBuffer>;
  hostVersion?: string;
}

const hosts = new Map<string, HostAPI>();

const SAFE_ASSET_ENTRY = /^[A-Za-z0-9._/-]+$/;

/** `entry` comes from the manifest (or, worse, from `frontend.entry` on a factory row a
 *  compromised backend could tamper with); it must resolve to a path strictly inside the
 *  plugin's own asset tree. A `..` segment (even URL-encoded — browsers normalise `%2e%2e`
 *  before the request leaves) would otherwise let it climb out of `/assets/<pluginId>/…` on
 *  the web route, or out of the Tauri `plugin://` scheme's asset root on desktop. */
export function assetUrl(pluginId: string, entry: string): string {
  const clean = entry.replace(/^\/+/, '').replace(/^frontend\/dist\//, '');
  if (!SAFE_ASSET_ENTRY.test(clean) || clean.split('/').includes('..')) {
    throw new Error(`assetUrl: unsafe entry "${entry}"`);
  }
  if (isTauri()) return `plugin://${pluginId}/${clean}`;
  return `${getApiBaseUrl()}/api/plugin-factory/${encodeURIComponent(pluginId)}/assets/${clean}`;
}

const defaultDigest = (bytes: ArrayBuffer) => crypto.subtle.digest('SHA-256', bytes);

async function sha256Base64(bytes: ArrayBuffer, digestImpl: LoaderDeps['digestImpl']): Promise<string> {
  const digest = await (digestImpl ?? defaultDigest)(bytes);
  let binary = '';
  for (const b of new Uint8Array(digest)) binary += String.fromCharCode(b);
  return `sha256-${btoa(binary)}`;
}

async function fetchVerified(url: string, integrity: string | undefined, deps: LoaderDeps): Promise<string> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const res = await fetchImpl(url, { headers: isTauri() ? {} : getAuthHeaders() });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const bytes = await res.arrayBuffer();
  if (integrity) {
    const actual = await sha256Base64(bytes, deps.digestImpl);
    if (actual !== integrity) throw new Error(`integrity mismatch for ${url} (expected ${integrity}, got ${actual})`);
  }
  return URL.createObjectURL(new Blob([bytes], { type: 'text/javascript' }));
}

function defaultImport(url: string): Promise<PluginModule> {
  return import(/* @vite-ignore */ url) as Promise<PluginModule>;
}

export function registerDeclaredUi(row: FactoryPluginRow): string[] {
  const events: string[] = [...(row.activation_events ?? [])];
  const ui = row.frontend?.ui;
  const owner = { owner: row.id };
  for (const page of ui?.pages ?? []) {
    const guard = page.guard ?? 'protected';
    const layout = page.layout ?? 'app';
    // `pageRouteElements` also drops this shape (defense in depth for anything reaching
    // PAGES another way), but rejecting it here means the illegal row never enters the
    // registry at all — no render-time surprise, and `PAGES.list()` stays a trustworthy view.
    if (layout === 'app' && guard !== 'protected') {
      reportUiError(new Error(`ui.pages: "${page.id}" is under /app and must declare guard "protected" (got "${guard}")`), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
      continue;
    }
    // M-2: plugin pages live under the shared `x/` namespace. This is what keeps a plugin's
    // page path from ever colliding with — or, worse, silently outranking — a builtin route: a
    // static plugin path like "agents/mine" would beat the builtin dynamic "agents/:agentId"
    // under react-router v6's specificity ranking (static beats dynamic) for any agent literally
    // named "mine". Confining plugins to `x/` makes that collision structurally impossible
    // instead of order- or data-dependent.
    if (!page.path.startsWith('x/')) {
      reportUiError(new Error(`ui.pages: "${page.id}" path "${page.path}" must start with "x/" (plugin pages live under the shared x/ namespace)`), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
      continue;
    }
    if (PAGES.has(page.id)) {
      // A plugin re-declaring ITS OWN already-registered page is a benign, expected no-op —
      // `loadPlugins()` can legitimately run more than once for the same plugin (e.g. once
      // unauthenticated with no rows, again after login re-fetches the real list; M-9). Only a
      // DIFFERENT owner claiming this id is an actual collision worth reporting.
      if (PAGES.ownerOf(page.id) !== row.id) {
        reportUiError(new Error(`ui.pages: "${page.id}" is already registered by "${PAGES.ownerOf(page.id)}"`), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
        continue;
      }
      events.push(`onPage:${page.id}`);
      continue;
    }
    // Two DIFFERENT page ids declaring the SAME path is not caught by the `has(id)` check above
    // — react-router v6 silently takes the first-registered <Route> for a duplicate path and the
    // second is permanently unreachable dead code with no signal to anyone.
    const pathOwner = PAGES.list().find((e) => e.value.path === page.path);
    if (pathOwner) {
      reportUiError(new Error(`ui.pages: path "${page.path}" is already used by "${pathOwner.id}" (owner "${pathOwner.owner}")`), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
      continue;
    }
    PAGES.register(page.id, { path: page.path, element: makePageGate(row.id, page.id), guard, layout }, owner);
    events.push(`onPage:${page.id}`);
  }
  for (const panel of ui?.panels ?? []) {
    if (!PANELS.has(panel.id)) PANELS.register(panel.id, { component: makePanelGate(row.id, panel.id) }, owner);
    events.push(`onPanel:${panel.id}`);
  }
  for (const kind of ui?.conversationKinds ?? []) {
    if (!CONVERSATION_KINDS.has(kind.id)) CONVERSATION_KINDS.register(kind.id, { labelKey: kind.label ?? kind.id }, owner);
  }
  for (const r of ui?.messageRenderers ?? []) {
    if (!MESSAGE_RENDERERS.has(r.id)) MESSAGE_RENDERERS.register(r.id, makeRendererGate(row.id, r.id, { role: r.role, contentPrefix: r.contentPrefix }), owner);
    events.push(`onRenderer:${r.id}`);
  }
  for (const te of ui?.timelineEvents ?? []) {
    if (!TIMELINE_EVENTS.has(te.type)) TIMELINE_EVENTS.register(te.type, { component: makeTimelineGate(row.id, te.id, te.type) }, owner);
    events.push(`onTimelineEvent:${te.id}`);
  }
  for (const kind of ui?.artifactKinds ?? []) {
    if (!ARTIFACT_KINDS.has(kind.id)) ARTIFACT_KINDS.register(kind.id, makeArtifactKindGate(row.id, kind.id, { label: kind.label, downloadExt: kind.downloadExt }), owner);
    events.push(`onArtifactKind:${kind.id}`);
  }
  for (const slot of ui?.slots ?? []) {
    const component = COMPONENT_SLOTS[slot.point];
    const action = ACTION_SLOTS[slot.point];
    if (component && !component.has(slot.id)) {
      component.register(slot.id, { component: makeSlotGate(row.id, slot.id), when: slot.when, order: slot.order }, owner);
    } else if (action && !action.has(slot.id)) {
      action.register(slot.id, makeActionGate(row.id, slot.id, action, slot.label ?? slot.id, slot.when, slot.order), owner);
    }
    events.push(`onSlot:${slot.id}`);
  }
  for (const cmd of ui?.commands ?? []) {
    if (!COMMANDS.has(cmd.id)) {
      // The gate object's own identity is the judge of "has the plugin replaced me yet?" —
      // comparing `label` (the previous approach) is wrong the moment a plugin registers its
      // real command with the same label it declared in the manifest (the natural thing to
      // do), which silently turns the click into a no-op. See `actionGate.ts` for the same
      // pattern on slot actions.
      const gate: CommandDef = {
        label: cmd.label,
        hint: cmd.hint,
        run: async () => {
          await fireActivation(`onCommand:${cmd.id}`);
          const real = COMMANDS.list().find((e) => e.id === cmd.id && e.owner === row.id && e.value !== gate);
          if (real) await real.value.run();
        },
      };
      COMMANDS.register(cmd.id, gate, owner);
    }
    events.push(`onCommand:${cmd.id}`);
  }
  return Array.from(new Set(events));
}

export async function activatePlugin(row: FactoryPluginRow, deps: LoaderDeps = {}): Promise<HostAPI> {
  const importImpl = deps.importImpl ?? defaultImport;
  if (!row.frontend) throw new Error(`${row.id}: no frontend entry`);
  // architecture E3(a): a "copy" install (a downloaded tarball/repo written into the plugin
  // store) has no other integrity guarantee — SRI is what pins the exact bytes an admin approved
  // to the exact bytes that get imported and run with full plugin privileges. A "link" install
  // points at a local dev checkout under active edit (re-verifying SRI on every edit would defeat
  // the point of a dev link) and is exempt, as is any row with no `mode` (older backend payloads
  // that do not yet send it) — this only fires on POSITIVE evidence of a copy install.
  if (row.mode === 'copy' && !row.frontend.integrity) {
    throw new Error(`${row.id}: missing integrity — a copy-installed plugin bundle must declare SRI`);
  }
  const url = assetUrl(row.id, row.frontend.entry);
  const blobUrl = await fetchVerified(url, row.frontend.integrity || undefined, deps);
  attributeChunkUrl(blobUrl, row.id);
  const mod = await importImpl(blobUrl);
  const activate = mod.plugin?.activate ?? mod.activate;
  if (typeof activate !== 'function') throw new Error(`${row.id}: bundle exports no activate(host)`);
  const host = createHostApi(row.id, deps.hostVersion ?? '');
  await activate.call(mod.plugin ?? mod, host);
  hosts.set(row.id, host);
  return host;
}

export function deactivatePlugin(pluginId: string): boolean {
  const host = hosts.get(pluginId);
  if (!host) return false;
  host.dispose();
  hosts.delete(pluginId);
  return true;
}

export function activeHosts(): string[] {
  return [...hosts.keys()];
}

/**
 * Whether every `loadPlugins()` pass in flight has finished (declared UI registered, `onStartup`
 * fired). A hard reload on a plugin page (`/app/x/<page>`) renders the route table before the
 * factory answers; `App.tsx` holds an unmatched `x/*` URL on a fallback until this settles instead
 * of redirecting to chat, otherwise every deep link into a plugin page was lost on refresh.
 */
let bootSettled = false;
/** Monotonic: true once ANY pass has settled, never false again (unlike `bootSettled`, which every new pass resets). */
let everSettled = false;
/** Passes in flight: the pre-mount pass and the login-flip pass can overlap; settle only at zero. */
let bootInFlight = 0;
const bootListeners = new Set<() => void>();
function setBootSettled(value: boolean): void {
  if (value) everSettled = true;
  if (bootSettled === value) return;
  bootSettled = value;
  for (const l of bootListeners) l();
}
/** The factory answer is awaited with a bound so a hung backend cannot hold a plugin deep link forever. */
export const FACTORY_FETCH_TIMEOUT_MS = 15_000;
export function pluginsBootSettled(): boolean {
  return bootSettled;
}
/**
 * Whether the FIRST `loadPlugins()` pass has finished — and it stays true across every later pass. Gate a
 * UI that must wait for the factory listing once (`BrowserNotices`) on this, not on `pluginsBootSettled()`:
 * the login-flip pass resets that one to false and the gated component would unmount and remount.
 * `App.tsx`'s plugin deep-link hold keeps using `pluginsBootSettled()`, which wants the per-pass reset.
 */
export function bootEverSettled(): boolean {
  return everSettled;
}
export function subscribePluginsBoot(listener: () => void): () => void {
  bootListeners.add(listener);
  return () => {
    bootListeners.delete(listener);
  };
}

/** Boot-time entry: register metadata for every loaded plugin, then fire onStartup. */
export async function loadPlugins(deps: LoaderDeps = {}): Promise<FactoryPluginRow[]> {
  bootInFlight += 1;
  setBootSettled(false);
  try {
    return await loadPluginsInner(deps);
  } finally {
    bootInFlight -= 1;
    if (bootInFlight === 0) setBootSettled(true);
  }
}

async function loadPluginsInner(deps: LoaderDeps): Promise<FactoryPluginRow[]> {
  exposeHostGlobals();
  const fetchImpl = deps.fetchImpl ?? fetch;
  let rows: FactoryPluginRow[] = [];
  try {
    const res = await fetchImpl(`${getApiBaseUrl()}/api/plugin-factory`, { headers: getAuthHeaders(), signal: AbortSignal.timeout(FACTORY_FETCH_TIMEOUT_MS) });
    if (!res.ok) return [];
    const body = (await res.json()) as { data?: { plugins?: FactoryPluginRow[]; builtins?: FactoryBuiltinRow[] } };
    for (const b of body.data?.builtins ?? []) {
      // "builtin.ui" is not a toggle-able builtin plugin package — it is the implicit DEFAULT
      // `owner` every shell registration gets when no explicit owner is passed (registry.ts).
      // Disabling it would blacklist that default owner forever and wipe every builtin page/
      // panel/sidebar/command currently registered under it, bricking the whole shell. The
      // factory always reports this row enabled+protected, but this guard does not trust that.
      if (b.id === 'builtin.ui') continue;
      if (b.excluded || (!b.enabled && !b.protected)) disableBuiltinUi(b.id);
    }
    rows = (body.data?.plugins ?? []).filter((r) => r.enabled && r.loaded && r.frontend);
  } catch (e) {
    reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'chunk', source: 'shell', context: 'loadPlugins' });
    return [];
  }
  for (const row of rows) {
    try {
      const events = registerDeclaredUi(row);
      registerActivation(row.id, events.length ? events : ['onStartup'], async () => {
        await activatePlugin(row, deps);
      });
    } catch (e) {
      reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
    }
  }
  await fireActivation('onStartup');
  return rows;
}
