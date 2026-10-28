/**
 * @file_name: netmindI18nDefaults.test.ts
 * @date: 2026-07-31
 * @description: Guard against drift between components' inline t() defaults
 * and en.json.
 *
 * NetmindAccountPanel.test.tsx mocks useTranslation to fall back to the
 * inline default, so the locale JSON values have zero test coverage: a copy
 * change applied to only one of the two places ships wrong text with every
 * test green. This test parses the Netmind settings components for
 * `t('settings.netmind.X', '<default>')` pairs and asserts each default
 * matches the en.json value verbatim.
 */

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
// Every component that authors settings.netmind.* copy. NetmindTopUpControls
// and PaymentMethodChoice were added 2026-08-18: the former had been carrying
// t() defaults with no en.json coverage since it was extracted, which is the
// exact drift this file exists to catch.
const componentFiles = [
  'NetmindActionZone.tsx',
  'NetmindAccountPanel.tsx',
  'NetmindUpsellCard.tsx',
  'NetmindTopUpControls.tsx',
  'PaymentMethodChoice.tsx',
  'NetmindProPurchase.tsx',
  // Added 2026-08-19: both author settings.netmind.* copy and had never been
  // listed, so the guard's own "every component" claim was false — the exact
  // hole it exists to close, and the same one NetmindTopUpControls sat in.
  'NetmindReturnNotice.tsx',
  'NetmindRunwayView.tsx',
].map((f) => resolve(here, '..', f));

const enJson = JSON.parse(
  readFileSync(resolve(here, '../../../i18n/locales/en.json'), 'utf8'),
) as { settings: { netmind: Record<string, string> } };

/** Every `t('settings.netmind.KEY', 'DEFAULT')` pair in the given source. */
function extractPairs(source: string): Array<{ key: string; dflt: string }> {
  // Second arg may be single- or double-quoted and may span a line break
  // after the comma; a third options arg may follow.
  const re =
    /\bt\(\s*'settings\.netmind\.([\w]+)'\s*,\s*('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")/g;
  const pairs: Array<{ key: string; dflt: string }> = [];
  for (const m of source.matchAll(re)) {
    const quoted = m[2];
    const dflt = quoted
      .slice(1, -1)
      .replace(quoted.startsWith("'") ? /\\'/g : /\\"/g, quoted[0] === "'" ? "'" : '"');
    pairs.push({ key: m[1], dflt });
  }
  return pairs;
}

describe('netmind settings i18n defaults', () => {
  it('inline t() defaults match en.json verbatim', () => {
    const mismatches: string[] = [];
    let total = 0;
    for (const file of componentFiles) {
      const source = readFileSync(file, 'utf8');
      for (const { key, dflt } of extractPairs(source)) {
        total += 1;
        const enValue = enJson.settings.netmind[key];
        if (enValue === undefined) {
          mismatches.push(`${key}: missing from en.json (inline: "${dflt}")`);
        } else if (enValue !== dflt) {
          mismatches.push(`${key}: en.json "${enValue}" != inline "${dflt}"`);
        }
      }
    }
    expect(mismatches).toEqual([]);
    // If the regex silently stops matching, the test would pass on nothing —
    // the components carry far more than a dozen t() calls today.
    expect(total).toBeGreaterThan(12);
  });
});


// The guard above only ever compared en.json. This feature's users are almost
// all on the Chinese UI, where a missing key silently falls back to the English
// default and a dropped {{placeholder}} renders a literal "{{total}}" on a
// payment button — both with every test green.
describe('zh.json parity', () => {
  const zh = JSON.parse(
    readFileSync(resolve(here, '../../../i18n/locales/zh.json'), 'utf8'),
  ) as { settings: { netmind: Record<string, string> } };

  it('has exactly the same settings.netmind keys as en.json', () => {
    expect(Object.keys(zh.settings.netmind).sort()).toEqual(
      Object.keys(enJson.settings.netmind).sort(),
    );
  });

  it('keeps every {{placeholder}} that en.json declares', () => {
    const holders = (s: string) => (s.match(/\{\{\w+\}\}/g) ?? []).sort();
    for (const [key, en] of Object.entries(enJson.settings.netmind)) {
      expect({ key, holders: holders(zh.settings.netmind[key] ?? '') })
        .toEqual({ key, holders: holders(en) });
    }
  });
});
