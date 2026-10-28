/**
 * @file_name: activation.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Frontend activation events — a plugin's bundle is imported and `activate(host)` runs on the first event it declared.
 *
 * Mirrors the kernel's activator: `onStartup` fires after the loader
 * registered every plugin's declarative metadata; `onPage:<id>`,
 * `onPanel:<id>`, `onCommand:<id>`, `onRenderer:<id>`, `onTimelineEvent:<id>`,
 * `onSlot:<id>`, `onArtifactKind:<id>` fire from the lazy gates the loader
 * put in the registries. Activation is once per plugin per page load; a
 * failure is reported to the error sink and remembered so the gate shows
 * the error instead of retrying in a loop.
 */
import { reportUiError } from './errorSink';

export type PluginActivate = () => Promise<void>;

interface Entry {
  events: Set<string>;
  activate: PluginActivate;
  state: 'pending' | 'active' | 'failed';
  error?: string;
  promise?: Promise<void>;
}

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
// Stable snapshots for useSyncExternalStore: a new object only when the state changed.
const snapshots = new Map<string, { state: 'pending' | 'active' | 'failed' | 'unknown'; error?: string }>();
const UNKNOWN = { state: 'unknown' as const };

export function registerActivation(pluginId: string, events: string[], activate: PluginActivate): void {
  entries.set(pluginId, { events: new Set(events), activate, state: 'pending' });
  notify();
}

export function activationState(pluginId: string): { state: 'pending' | 'active' | 'failed' | 'unknown'; error?: string } {
  const e = entries.get(pluginId);
  if (!e) return UNKNOWN;
  const cached = snapshots.get(pluginId);
  if (cached && cached.state === e.state && cached.error === e.error) return cached;
  const next = { state: e.state, error: e.error };
  snapshots.set(pluginId, next);
  return next;
}

export function subscribeActivation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify(): void {
  for (const l of listeners) l();
}

async function run(pluginId: string, e: Entry): Promise<void> {
  if (e.state !== 'pending') return;
  if (e.promise) return e.promise;
  e.promise = (async () => {
    try {
      await e.activate();
      e.state = 'active';
    } catch (err) {
      e.state = 'failed';
      e.error = err instanceof Error ? err.message : String(err);
      reportUiError(err instanceof Error ? err : new Error(String(err)), { kind: 'chunk', source: pluginId, context: 'activate' });
    } finally {
      notify();
    }
  })();
  return e.promise;
}

/** Activate every pending plugin subscribed to `event`; never throws. */
export async function fireActivation(event: string): Promise<string[]> {
  const fired: string[] = [];
  for (const [pluginId, e] of entries) {
    if (e.state === 'pending' && e.events.has(event)) {
      fired.push(pluginId);
      await run(pluginId, e);
    }
  }
  return fired;
}

/** Explicit (re)activation from the factory page. */
export async function activateNow(pluginId: string): Promise<void> {
  const e = entries.get(pluginId);
  if (!e) throw new Error(`plugin ${pluginId} is not registered for activation`);
  if (e.state === 'failed') {
    e.state = 'pending';
    e.error = undefined;
    e.promise = undefined;
  }
  await run(pluginId, e);
}

/** Test hook. */
export function resetActivation(): void {
  entries.clear();
  listeners.clear();
  snapshots.clear();
}
