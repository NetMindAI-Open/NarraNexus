/**
 * @file_name: chatDays.ts
 * @author: Bin Liang
 * @date: 2026-07-23
 * @description: Day-separator helpers for the chat timeline.
 *
 * Chat messages show only HH:mm:ss, so a history spanning several days
 * read as one undated stream. The timeline inserts a separator whenever
 * the LOCAL calendar day changes; today/yesterday get relative labels
 * (i18n'd by the caller), older days a locale-formatted date.
 */

export interface ChatDayInfo {
  /** Local calendar-day grouping key (stable within one day). */
  key: string;
  /** How the caller should label the separator. */
  kind: 'today' | 'yesterday' | 'date';
  /** Locale-formatted date — meaningful for kind === 'date'. */
  label: string;
}

function localDayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

/**
 * Classify a message timestamp into its local calendar day.
 *
 * Args:
 *   ts: Message timestamp (ms epoch).
 *   now: "Current" date — injectable for tests; defaults to the real clock.
 */
export function chatDayInfo(ts: number, now: Date = new Date()): ChatDayInfo {
  const d = new Date(ts);
  const key = localDayKey(d);

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);

  const kind: ChatDayInfo['kind'] =
    key === localDayKey(now) ? 'today'
    : key === localDayKey(yesterday) ? 'yesterday'
    : 'date';

  const label = d.toLocaleDateString(undefined, {
    year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric',
    month: 'short',
    day: 'numeric',
  });

  return { key, kind, label };
}
