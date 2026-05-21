/**
 * NM HTML contract test — verifies index.html does NOT load any web fonts.
 * NM uses pure system font stack (Axiom #7). Loading a web font here would
 * regress FCP and contradict the design system.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, test, expect } from 'vitest';

const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf-8');

describe('NM index.html — no web fonts', () => {
  test('no preconnect to Google Fonts', () => {
    expect(html).not.toMatch(/fonts\.googleapis\.com/);
    expect(html).not.toMatch(/fonts\.gstatic\.com/);
  });

  test('no Space Grotesk web font load', () => {
    expect(html).not.toMatch(/Space\+?Grotesk/);
  });

  test('no Barlow web font load', () => {
    expect(html).not.toMatch(/Barlow/);
  });

  test('no DM Mono web font load', () => {
    expect(html).not.toMatch(/DM\+?Mono/);
  });

  test('no Inter web font load', () => {
    expect(html).not.toMatch(/family=Inter/);
  });

  test('NM marker comment present (for human reviewers)', () => {
    expect(html).toMatch(/NM design system|system font stack|no web font/i);
  });
});
