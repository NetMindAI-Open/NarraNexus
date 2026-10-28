/**
 * @file_name: locale-parity.test.ts
 * @date: 2026-09-03
 * @description: No locale may drift further from en.json than it already has.
 *
 * i18next's fallbackLng only covers a MISSING key; a key that exists with
 * stale content is served as-is forever. The 2026-09-03 team-room change
 * shipped new keys and a corrected sentence in 2 of 10 locales before review
 * caught it. Full parity is not the current state — eight locales were
 * already ~480 keys behind en.json (see the baseline) — so this test pins the
 * BASELINE and fails on any NEW gap: a key added to en.json must be added to
 * every locale, and a key removed from en.json must be removed everywhere.
 * Shrinking the baseline (translating a backlog key) is always allowed;
 * regenerate it with the snippet in the baseline's sibling mirror md.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import baseline from './locale-parity.baseline.json';

const dir = join(__dirname, '..', 'i18n', 'locales');

function flatten(obj: unknown, prefix = ''): string[] {
  if (obj === null || typeof obj !== 'object') return [prefix];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    flatten(v, prefix ? `${prefix}.${k}` : k),
  );
}

const en = new Set(flatten(JSON.parse(readFileSync(join(dir, 'en.json'), 'utf8'))));
const locales = readdirSync(dir)
  .filter((f) => f.endsWith('.json') && f !== 'en.json')
  .map((f) => f.slice(0, -5));

type Gap = { missing: string[]; extra: string[] };
const known = baseline as Record<string, Gap>;

describe('locale key parity (no new drift from en.json)', () => {
  it.each(locales)('%s adds no new missing or extra keys', (loc) => {
    const keys = new Set(flatten(JSON.parse(readFileSync(join(dir, `${loc}.json`), 'utf8'))));
    const gap = known[loc] ?? { missing: [], extra: [] };
    const knownMissing = new Set(gap.missing);
    const knownExtra = new Set(gap.extra);
    const newMissing = [...en].filter((k) => !keys.has(k) && !knownMissing.has(k));
    const newExtra = [...keys].filter((k) => !en.has(k) && !knownExtra.has(k));
    expect({ newMissing, newExtra }).toEqual({ newMissing: [], newExtra: [] });
  });

  it('zh is at full parity with en', () => {
    expect(known.zh).toEqual({ missing: [], extra: [] });
  });
});
