/**
 * @file_name: browserLook.ts
 * @author:
 * @date: 2026-09-24
 * @description: Read a persisted browser_look tool output into the few facts its timeline row shows.
 *
 * One tool result, three persisted shapes, because each agent executor
 * flattens MCP results differently: NexusPower keeps the metadata JSON
 * verbatim; Claude Code concatenates the metadata JSON with an image
 * descriptor JSON (no separator); Codex serialises the whole MCP result, so
 * the metadata is the text of the first `content` part. The image bytes are
 * never in any of them (the backend replaces them with a descriptor).
 * Returns null for anything unrecognised so the generic row renders instead.
 */

export interface BrowserLookSummary {
  ok: boolean;
  /** Failure reason (failed looks only). */
  message?: string;
  title?: string;
  url?: string;
  /** Pixel size of the image the model received. */
  image?: { width: number; height: number };
  /** Captured area in CSS pixels — absent when the whole viewport was captured. */
  region?: { width: number; height: number };
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const size = (value: unknown): { width: number; height: number } | undefined =>
  isObject(value) && typeof value.width === 'number' && typeof value.height === 'number'
    ? { width: Math.round(value.width), height: Math.round(value.height) }
    : undefined;

/** The first complete JSON object at the start of `text` (ignoring leading
 *  whitespace), found by brace depth with string/escape awareness. */
function leadingObject(text: string): unknown {
  const start = text.search(/\S/);
  if (start < 0 || text[start] !== '{') return undefined;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
    } else if (ch === '"') {
      inString = true;
    } else if (ch === '{') {
      depth++;
    } else if (ch === '}' && --depth === 0) {
      try {
        return JSON.parse(text.slice(start, i + 1));
      } catch {
        return undefined;
      }
    }
  }
  return undefined;
}

function metadataOf(value: unknown): Json | undefined {
  if (!isObject(value)) return undefined;
  if (typeof value.outcome === 'string') return value;
  // Codex: the MCP result object, metadata in the first text part.
  if (Array.isArray(value.content)) {
    const text = value.content.find((part) => isObject(part) && part.type === 'text' && typeof part.text === 'string');
    return text ? metadataOf(leadingObject((text as Json).text as string)) : undefined;
  }
  return undefined;
}

export function parseBrowserLookOutput(output: string): BrowserLookSummary | null {
  const meta = metadataOf(leadingObject(output));
  if (!meta) return null;
  if (meta.outcome !== 'OK') {
    return { ok: false, message: typeof meta.message === 'string' ? meta.message : undefined };
  }
  const summary: BrowserLookSummary = { ok: true };
  if (typeof meta.title === 'string' && meta.title) summary.title = meta.title;
  if (typeof meta.url === 'string' && meta.url) summary.url = meta.url;
  const image = size(meta.image);
  if (image) summary.image = image;
  const region = size(meta.region);
  const viewport = size(meta.viewport);
  const rx = isObject(meta.region) ? meta.region.x : undefined;
  const ry = isObject(meta.region) ? meta.region.y : undefined;
  const wholeViewport = !!region && !!viewport && rx === 0 && ry === 0
    && region.width === viewport.width && region.height === viewport.height;
  if (region && !wholeViewport) summary.region = region;
  return summary;
}

/** `ToolRendererDef.accepts` for browser_look: only outputs its row can actually read. */
export function acceptsBrowserLookOutput(output: string): boolean {
  return parseBrowserLookOutput(output) !== null;
}
