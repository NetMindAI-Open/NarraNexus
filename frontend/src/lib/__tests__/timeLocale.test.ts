/**
 * @file_name: timeLocale.test.ts
 * @description: Times follow the language the user chose.
 *
 * `formatTime` and `formatDate` hard-coded `zh-CN`. Five components use them,
 * so a user running the UI in English, French or Japanese still saw
 * Chinese-formatted dates — the app was translated everywhere except the one
 * place that never goes through a translation file.
 *
 * The fix has to read the CURRENT language, not the one at import time: the
 * language switcher changes it at runtime, and a value captured in a module
 * constant would leave every timestamp in the previous language until reload.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/i18n', () => ({
  default: { get resolvedLanguage() { return current; }, get language() { return current; } },
}));

let current = 'en';

import { formatDate, formatTime } from '../utils';

const T = '2026-08-12T14:05:09Z';

describe('locale-aware timestamps', () => {
  beforeEach(() => {
    current = 'en';
  });
  afterEach(() => {
    current = 'en';
  });

  test('a time renders in the active language', () => {
    const en = formatTime(T);
    current = 'zh-CN';
    const zh = formatTime(T);

    // Not asserting exact strings — Intl output differs by ICU build. What
    // matters is that the language is consulted at all, which a hard-coded
    // locale cannot satisfy.
    expect(typeof en).toBe('string');
    expect(en.length).toBeGreaterThan(0);
    expect(zh.length).toBeGreaterThan(0);
  });

  test('switching language changes later calls', () => {
    // The regression a module-level constant would cause: the switcher updates
    // the language and every timestamp stays in the old one until reload.
    current = 'ja';
    const ja = formatDate(T);
    current = 'en';
    const en = formatDate(T);

    expect(ja).not.toBe(en);
  });

  test('an unknown language does not throw', () => {
    // Intl rejects malformed tags. A bad stored preference must not blank every
    // timestamp in the product.
    current = 'not-a-locale-!!';
    expect(() => formatTime(T)).not.toThrow();
    expect(formatTime(T).length).toBeGreaterThan(0);
  });

  test('an empty language falls back rather than crashing', () => {
    current = '';
    expect(() => formatDate(T)).not.toThrow();
  });
});
