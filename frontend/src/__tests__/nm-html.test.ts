/**
 * NM HTML contract test — fonts are SELF-HOSTED (Space Grotesk / Inter / DM
 * Mono, aligned with the marketing site), loaded from /fonts/narra-fonts.css.
 * They must never come from a third-party CDN: self-hosting keeps FCP fast,
 * works offline / in the desktop DMG, and avoids a Google Fonts privacy hop.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, test, expect } from 'vitest';

const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf-8');

describe('NM index.html — fonts are self-hosted, no third-party CDN', () => {
  test('no preconnect / load from Google Fonts', () => {
    expect(html).not.toMatch(/fonts\.googleapis\.com/);
    expect(html).not.toMatch(/fonts\.gstatic\.com/);
  });

  test('no Google-Fonts family URLs (the +-encoded form)', () => {
    expect(html).not.toMatch(/Space\+Grotesk/);
    expect(html).not.toMatch(/DM\+Mono/);
    expect(html).not.toMatch(/family=Inter/);
    expect(html).not.toMatch(/Barlow/);
  });

  test('self-hosted font stylesheet is linked', () => {
    expect(html).toMatch(/\/fonts\/narra-fonts\.css/);
  });
});
