/**
 * main.tsx contract test — verifies the ECharts NM theme registration is
 * imported at app boot. Reading the file source rather than executing the
 * module avoids bootstrapping React + Vite in jsdom.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, test, expect } from 'vitest';

const main = readFileSync(resolve(__dirname, '../main.tsx'), 'utf-8');

describe('main.tsx — NM init', () => {
  test('imports the echarts NM theme module', () => {
    expect(main).toMatch(/import\s+['"]\.\/lib\/echarts-nm-theme['"]/);
  });

  test('imports index.css (preserved from baseline)', () => {
    expect(main).toMatch(/import\s+['"]\.\/index\.css['"]/);
  });

  test('NM imports come before App import', () => {
    const themeIdx = main.search(/import\s+['"]\.\/lib\/echarts-nm-theme['"]/);
    const appIdx = main.search(/import\s+App\s+from/);
    expect(themeIdx).toBeGreaterThan(-1);
    expect(appIdx).toBeGreaterThan(-1);
    expect(themeIdx).toBeLessThan(appIdx);
  });
});
