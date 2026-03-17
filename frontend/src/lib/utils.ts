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
 * Generate a unique ID
 */
export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Parse timestamp to Date, treating timezone-naive strings as UTC.
 * Backend stores UTC timestamps without 'Z' suffix (e.g. "2026-03-11 09:50:09"),
 * which JS would otherwise parse as local time, causing wrong display in other timezones.
 */
function parseUTCTimestamp(timestamp: number | string): Date {
  if (typeof timestamp === 'number') return new Date(timestamp);
  const s = timestamp.trim();
  if (/[Z+-]\d{0,4}$/.test(s)) return new Date(s);
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
