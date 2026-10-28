/**
 * @file_name: themeTokens.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: themeTokens.generated.ts matches the custom properties declared in index.css (regenerate with scripts/dev/gen_theme_tokens.py --write).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

import { THEME_TOKENS } from '@/platform/registries/themeTokens.generated';

it('generated token list is fresh', () => {
  const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const line of css.split('\n')) {
    const m = line.match(/^\s*(--[a-zA-Z0-9-]+)\s*:/);
    if (m && !seen.has(m[1])) {
      seen.add(m[1]);
      tokens.push(m[1]);
    }
  }
  expect([...THEME_TOKENS]).toEqual(tokens);
});
