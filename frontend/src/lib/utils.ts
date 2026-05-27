/**
 * Utility functions
 */

import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge Tailwind classes with clsx
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Generate a unique ID.
 *
 * Uses a monotonic counter in addition to Date.now() so that IDs generated
 * within the same millisecond are still totally ordered. Downstream sorts
 * can use the trailing counter segment as a tie-breaker when timestamps
 * collide (e.g. rapid user messages, assistant error + next user send).
 */
let _idCounter = 0;
export function generateId(): string {
  _idCounter = (_idCounter + 1) & 0xffffff; // wrap at 16M, still monotonic per-ms
  return `${Date.now()}-${_idCounter.toString(36)}-${Math.random().toString(36).substr(2, 6)}`;
}

/**
 * Parse timestamp to Date, treating timezone-naive strings as UTC.
 * Backend stores UTC timestamps without 'Z' suffix (e.g. "2026-03-11 09:50:09"),
 * which JS would otherwise parse as local time, causing wrong display in other timezones.
 */
function parseUTCTimestamp(timestamp: number | string): Date {
  if (typeof timestamp === 'number') return new Date(timestamp);
  const s = timestamp.trim();
  // Already has timezone info: Z, +HHMM, -HHMM, +HH:MM, -HH:MM
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(s)) return new Date(s);
  // No timezone — assume UTC
  return new Date(s.replace(' ', 'T') + 'Z');
}

/**
 * Format timestamp to readable string
 */
export function formatTime(timestamp: number | string): string {
  const date = parseUTCTimestamp(timestamp);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * Format date to readable string
 */
export function formatDate(timestamp: number | string): string {
  const date = parseUTCTimestamp(timestamp);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

/**
 * IM-sidebar-style timestamp for conversation lists (AgentList).
 *
 * Plain `formatTime` (HH:MM:SS) on the sidebar made messages from days
 * ago indistinguishable from "this morning" — the user only saw "14:23"
 * with no date context. This formatter is calendar-aware, mirroring
 * WeChat / Lark / Slack:
 *
 *   today              → "14:23"     (no seconds — sidebar is glance-able)
 *   yesterday          → "Yesterday"
 *   2..6 days back     → "Wed"       (en-US short weekday)
 *   older, same year   → "May 18"
 *   previous year+     → "2025/05/18"
 *
 * Each branch returns exactly one of {time, weekday, date} so the row
 * never gets ambiguous. "14:23" is always today; "Yesterday" is always
 * yesterday; a weekday name is always within the past week.
 *
 * Locale note: project rule is no Chinese strings in code, so even the
 * 24h time uses en-GB (always 24h without the AM/PM artifact some
 * en-US Node builds emit even with hour12: false).
 */
export function formatChatTimestamp(timestamp: number | string): string {
  const date = parseUTCTimestamp(timestamp);
  const now = new Date();

  // Calendar-day comparison in the user's local timezone — `diff_ms / 86400000`
  // would mis-classify e.g. a 14:00 → 02:00 next-day pair as "same day".
  const startOfLocalDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayDiff = Math.round(
    (startOfLocalDay(now) - startOfLocalDay(date)) / 86_400_000,
  );

  if (dayDiff === 0) {
    return date.toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  if (dayDiff === 1) {
    return 'Yesterday';
  }
  if (dayDiff >= 2 && dayDiff < 7) {
    return date.toLocaleDateString('en-US', { weekday: 'short' });
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  // Cross-year: explicit YYYY/MM/DD so the year is unambiguous.
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}/${m}/${d}`;
}

/**
 * Format relative time (e.g., "2 minutes ago" or "in 3 hours")
 * Handles both past and future dates
 */
export function formatRelativeTime(timestamp: number | string): string {
  const date = parseUTCTimestamp(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const isFuture = diffMs < 0;
  const absDiffMs = Math.abs(diffMs);
  const diffSec = Math.floor(absDiffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (isFuture) {
    // 未来时间
    if (diffSec < 60) return 'in a moment';
    if (diffMin < 60) return `in ${diffMin}m`;
    if (diffHour < 24) return `in ${diffHour}h`;
    if (diffDay < 7) return `in ${diffDay}d`;
    return formatDate(timestamp);
  } else {
    // 过去时间
    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return formatDate(timestamp);
  }
}

/**
 * Truncate text with ellipsis
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 3) + '...';
}
