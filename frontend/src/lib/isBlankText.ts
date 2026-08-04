/**
 * @file_name: isBlankText.ts
 * @date: 2026-08-04
 * @description: The single blank-text predicate for the blank-bubble guards.
 *
 * "Blank = no reply" is one line drawn across four layers (extract /
 * persist / session / timeline). The frontend consumers (chatStore reply
 * extraction, buildTimeline history filter) share this predicate so the
 * line cannot drift between call sites.
 */
export function isBlankText(s?: string | null): boolean {
  return !s?.trim();
}
