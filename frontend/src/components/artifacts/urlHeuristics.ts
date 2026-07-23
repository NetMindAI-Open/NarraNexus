/**
 * @file_name: urlHeuristics.ts
 * @description: URL-detection heuristics for the new-tab omnibox — decides
 * "open as a URL" vs "search existing artifacts", and normalizes a bare host.
 * Kept out of the component file so it exports plain functions (react-refresh).
 */

/** True if `text` looks like a URL we can open directly. */
export function looksLikeUrl(text: string): boolean {
  const s = text.trim();
  if (!s || /\s/.test(s)) return false;
  if (/^https?:\/\//i.test(s)) return true;
  // bare host like example.com/path — require a dot and a TLD-ish tail
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+(\/.*)?$/i.test(s);
}

/** Normalize a bare host to an https URL; leave an explicit scheme untouched. */
export function normalizeUrl(text: string): string {
  const s = text.trim();
  return /^https?:\/\//i.test(s) ? s : `https://${s}`;
}
