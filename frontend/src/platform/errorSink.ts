/**
 * @file_name: errorSink.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Where UI crashes are reported, attributed to the code that owns them.
 *
 * Until now a render crash only reached `console.error`. The sink keeps the
 * console output and adds subscribers (batch 2 wires the plugin factory's
 * health view and a backend report) plus attribution. Attribution is a
 * best-effort heuristic: the plugin loader records each plugin's chunk URL
 * prefix, and a crash whose `error.stack` text contains that prefix is
 * blamed on the plugin; anything else is "shell". Stacks are not
 * normalised across browsers, so this can under-attribute (never
 * mis-attribute to another plugin unless two prefixes overlap).
 */

export interface UiErrorReport {
  source: string; // "shell" or a plugin id
  error: Error;
  componentStack?: string;
  kind: 'render' | 'chunk';
  at: number;
}

type Listener = (report: UiErrorReport) => void;

const listeners = new Set<Listener>();
const recent: UiErrorReport[] = [];
const RECENT_LIMIT = 50;
const pluginUrls = new Map<string, string>(); // chunk URL prefix -> plugin id

export function attributeChunkUrl(urlPrefix: string, pluginId: string): void {
  pluginUrls.set(urlPrefix, pluginId);
}

export function sourceFor(error: Error): string {
  const stack = error.stack ?? '';
  for (const [prefix, pluginId] of pluginUrls) {
    if (stack.includes(prefix)) return pluginId;
  }
  return 'shell';
}

export interface ReportOptions {
  componentStack?: string;
  kind?: 'render' | 'chunk';
  /** Known attribution (the loader/host know which plugin they were serving). */
  source?: string;
  context?: string;
}

type Poster = (pluginId: string, body: { kind: string; message: string; stack: string }) => Promise<unknown>;
let poster: Poster | null = null;
const postedAt = new Map<string, number>();
const POST_MIN_INTERVAL_MS = 2000;

/** Install the backend reporter (the app wires `/api/plugin-factory/{id}/errors`); null disables. */
export function setErrorPoster(fn: Poster | null): void {
  poster = fn;
}

let dispatching = false;
// Reports raised while a report is being dispatched (a listener or the poster's
// failure path reporting something else). They are delivered after the current
// dispatch, in order, bounded so a listener that reports on every report still
// terminates.
const pending: UiErrorReport[] = [];

export function reportUiError(error: Error, opts: ReportOptions = {}): UiErrorReport {
  const report: UiErrorReport = {
    source: opts.source ?? sourceFor(error),
    error,
    componentStack: opts.componentStack,
    kind: opts.kind ?? 'render',
    at: Date.now(),
  };
  recent.push(report);
  if (recent.length > RECENT_LIMIT) recent.shift();
  const context = opts.context;
  // A listener (or the poster's failure path) that itself reports must not
  // recurse: while dispatching, nested reports are queued and delivered after
  // the current one — to the subscribers AND to the poster — not dropped.
  if (dispatching) {
    pending.push(report);
    return report;
  }
  dispatching = true;
  try {
    deliver(report, context);
    let drained = 0;
    while (pending.length && drained < RECENT_LIMIT) {
      deliver(pending.shift()!, undefined);
      drained += 1;
    }
    pending.length = 0; // beyond the bound: a report loop, cut here (the entries are still in `recent`)
  } finally {
    dispatching = false;
  }
  return report;
}

function deliver(report: UiErrorReport, context: string | undefined): void {
  for (const l of listeners) {
    try {
      l(report);
    } catch {
      // a broken listener must not mask the original error
    }
  }
  if (poster && report.source !== 'shell') {
    // Throttled per plugin: a render loop must not become a request loop.
    const last = postedAt.get(report.source) ?? 0;
    if (report.at - last >= POST_MIN_INTERVAL_MS) {
      postedAt.set(report.source, report.at);
      void poster(report.source, {
        kind: report.kind,
        message: `${context ? context + ': ' : ''}${report.error.message}`,
        stack: report.error.stack ?? '',
      }).catch(() => undefined);
    }
  }
}

export function onUiError(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** A snapshot of the last reports (newest last); the sink's own buffer is not exposed. */
export function recentUiErrors(): readonly UiErrorReport[] {
  return [...recent];
}

/**
 * Clear attribution, history, subscribers and the poster. Only tests call it
 * (the sink is a module singleton shared by every test file through
 * `test-setup.ts`); it stays exported from here rather than a test-only
 * module so the reset and the state it resets cannot drift apart.
 */
export function resetErrorSink(): void {
  listeners.clear();
  recent.length = 0;
  pluginUrls.clear();
  pending.length = 0;
  poster = null;
  postedAt.clear();
}
