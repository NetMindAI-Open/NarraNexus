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
const componentFiles = [
  'NetmindActionZone.tsx',
  'NetmindAccountPanel.tsx',
  'NetmindUpsellCard.tsx',
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
