/**
 * @file_name: bootstrap.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The app entry registers the shell's contributions before rendering; the test setup mirrors it.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => readFileSync(resolve(__dirname, '../../..', rel), 'utf8');

describe('shell bootstrap', () => {
  it('main.tsx imports platform/builtin before App', () => {
    const main = read('src/main.tsx');
    const builtinAt = main.indexOf("import './platform/builtin'");
    const appAt = main.indexOf("import App from './App");
    expect(builtinAt).toBeGreaterThan(-1);
    expect(appAt).toBeGreaterThan(-1);
    expect(builtinAt).toBeLessThan(appAt);
  });

  it('test-setup.ts loads the same builtin registrations', () => {
    expect(read('test-setup.ts')).toContain("import './src/platform/builtin'");
  });

  it('SettingsPage registers the builtin sections from its own chunk', () => {
    expect(read('src/pages/SettingsPage.tsx')).toContain("import './settings/registerBuiltinSections'");
  });
});
