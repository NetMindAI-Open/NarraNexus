/**
 * @file_name: officeEditCommands.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: The T1 office direct-edit vocabulary (spec B §3.3) — builders
 * for officecli watch `/api/batch` items and the parser for the watch page's
 * selection reports. Verified against officecli 1.0.144 (2026-08-19 probe):
 * batch items are {command, path, props?}; `set` carries changes in `props`
 * (text / bold / italic / color); selections arrive as {"paths": [...]}.
 * Every surface that talks to the watch edit API goes through this module so
 * the wire format lives in exactly one place.
 */

export interface WatchCommand {
  command: 'remove' | 'set';
  path: string;
  props?: Record<string, string | boolean>;
}

export function buildRemoveCommands(paths: string[]): WatchCommand[] {
  return paths.map((path) => ({ command: 'remove', path }));
}

export function buildSetPropsCommands(
  paths: string[],
  props: Record<string, string | boolean>,
): WatchCommand[] {
  return paths.map((path) => ({ command: 'set', path, props }));
}

export function buildSetTextCommand(path: string, text: string): WatchCommand {
  return { command: 'set', path, props: { text } };
}

/** Parse the selection body the watch page POSTs to /api/selection. */
export function parseSelectionMessage(body: string): string[] {
  try {
    const parsed = JSON.parse(body);
    const paths = Array.isArray(parsed) ? parsed : parsed?.paths;
    if (!Array.isArray(paths)) return [];
    return paths.filter((p): p is string => typeof p === 'string');
  } catch {
    return [];
  }
}

// ── T2 vocabulary (verified against officecli 1.0.144, 2026-08-19 probe) ──

export interface WatchMoveCommand {
  command: 'move';
  path: string;
  index: number;
}

export interface WatchAddCommand {
  command: 'add';
  parent: string;
  type: 'row' | 'column';
  index?: number;
}

export function buildMoveCommand(path: string, index: number): WatchMoveCommand {
  return { command: 'move', path, index };
}

export function buildAddCommand(
  parent: string,
  type: 'row' | 'column',
  index?: number,
): WatchAddCommand {
  return index === undefined
    ? { command: 'add', parent, type }
    : { command: 'add', parent, type, index };
}

export function buildSetFormulaCommand(path: string, formula: string): WatchCommand {
  return { command: 'set', path, props: { formula } };
}

export function buildSetSrcCommand(path: string, src: string): WatchCommand {
  return { command: 'set', path, props: { src } };
}

/** /slide[N] (a whole slide, nothing deeper) → N; anything else → null. */
export function slideIndexFromPath(path: string): number | null {
  const m = /^\/slide\[(\d+)\]$/.exec(path);
  return m ? Number(m[1]) : null;
}

/** /SheetName/A1-style cell → {sheet, row}; anything else → null. */
export function cellFromPath(path: string): { sheet: string; row: number } | null {
  const m = /^(\/[^/]+)\/([A-Z]+)(\d+)$/.exec(path);
  if (!m) return null;
  // A bracketed segment (slide[1], p[2]) is a DOM path, not a sheet name.
  if (m[1].includes('[')) return null;
  return { sheet: m[1], row: Number(m[3]) };
}

/** pptx picture element (pic[N] or picture[@id=...]). */
export function isPicturePath(path: string): boolean {
  return /\/(pic|picture)\[[^\]]+\]$/.test(path);
}
