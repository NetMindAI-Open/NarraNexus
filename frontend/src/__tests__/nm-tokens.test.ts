/**
 * NM token contract test — verifies index.css declares the required NM tokens
 * and contains none of the banned Archive tokens. CSS-as-text grep tests
 * because jsdom does not reliably resolve @theme tokens at runtime.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, test, expect } from 'vitest';

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf-8');

describe('NM @theme tokens', () => {
  test('species colors declared', () => {
    expect(css).toMatch(/--color-carbon:\s*#E8704A/);
    expect(css).toMatch(/--color-silicon:\s*#3D7EC4/);
    expect(css).toMatch(/--color-overlap:\s*#8E5CB8/);
  });

  test('Tailwind gray scale remapped to warm palette', () => {
    expect(css).toMatch(/--color-gray-50:\s*#FAFAF7/);
    expect(css).toMatch(/--color-gray-100:\s*#F2EFE8/);
    expect(css).toMatch(/--color-gray-900:\s*#1A1612/);
  });

  test('radius scale (compressed editorial values)', () => {
    expect(css).toMatch(/--radius-xs:\s*2px/);
    expect(css).toMatch(/--radius-md:\s*4px/);
    expect(css).toMatch(/--radius-lg:\s*4px/);
    expect(css).toMatch(/--radius-xl:\s*6px/);
  });

  test('warm status palette declared', () => {
    expect(css).toMatch(/--color-red-500:\s*#C95A4D/);
    expect(css).toMatch(/--color-yellow-500:\s*#C49A3E/);
    expect(css).toMatch(/--color-green-500:\s*#6B9466/);
  });

  test('motion easing + duration tokens declared', () => {
    expect(css).toMatch(/--ease-paper:/);
    expect(css).toMatch(/--motion-fast:\s*150ms/);
    expect(css).toMatch(/--motion-medium:\s*280ms/);
    expect(css).toMatch(/--motion-slow:\s*480ms/);
  });
});

describe('NM :root (light) tokens', () => {
  test('paper warm tones', () => {
    expect(css).toMatch(/:root\s*\{[\s\S]*?--nm-paper:\s*#FAFAF7/);
    expect(css).toMatch(/:root\s*\{[\s\S]*?--nm-paper-warm:\s*#F2EFE8/);
    expect(css).toMatch(/:root\s*\{[\s\S]*?--nm-raised:\s*#F5F2EB/);
  });

  test('warm ink ramp', () => {
    expect(css).toMatch(/:root\s*\{[\s\S]*?--nm-ink:\s*#2A2620/);
    expect(css).toMatch(/:root\s*\{[\s\S]*?--nm-ink70:\s*rgba\(42,38,32,0\.70\)/);
  });

  test('Archive semantic tokens rebound to NM values', () => {
    expect(css).toMatch(/:root\s*\{[\s\S]*?--bg-primary:\s*var\(--nm-card\)/);
    expect(css).toMatch(/:root\s*\{[\s\S]*?--text-primary:\s*var\(--nm-ink\)/);
    expect(css).toMatch(/:root\s*\{[\s\S]*?--rule:\s*var\(--nm-hairline\)/);
  });
});

describe('NM .dark (warm-ink) tokens', () => {
  test('warm dark paper', () => {
    expect(css).toMatch(/\.dark\s*\{[\s\S]*?--nm-paper:\s*#1A1612/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*?--nm-paper-warm:\s*#211C16/);
  });

  test('species lifted for AA', () => {
    expect(css).toMatch(/\.dark\s*\{[\s\S]*?--color-carbon:\s*#FF7A5C/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*?--color-silicon:\s*#7BB1E8/);
  });
});

describe('NM font stack', () => {
  // Self-hosted web fonts aligned with the marketing site (narranexus-website):
  // Space Grotesk (display) / Inter (sans) / DM Mono (mono), each with a CJK
  // + system fallback chain. Loaded via /fonts/narra-fonts.css (see index.html).
  test('display font: Space Grotesk + CJK fallback', () => {
    expect(css).toMatch(/--font-display:[^;]*Space Grotesk[^;]*PingFang SC[^;]*Noto Sans CJK SC/);
  });

  test('sans font: Inter + CJK fallback', () => {
    expect(css).toMatch(/--font-sans:[^;]*Inter[^;]*PingFang SC[^;]*Noto Sans CJK SC/);
  });

  test('mono font: DM Mono + system fallback', () => {
    expect(css).toMatch(/--font-mono:[^;]*DM Mono[^;]*ui-monospace/);
  });
});

describe('Archive tokens REMOVED (banned)', () => {
  // Space Grotesk + DM Mono are now intentional (self-hosted, see the NM font
  // stack above) — they are no longer banned. Barlow / cold-gray / pure-black /
  // paper-grid remain retired.
  test('no Barlow reference', () => {
    expect(css).not.toMatch(/Barlow/);
  });

  test('no cold gray paper #f1f3f5', () => {
    expect(css).not.toMatch(/#f1f3f5|#F1F3F5/);
  });

  test('no pure black ink #000000 as color-ink', () => {
    expect(css).not.toMatch(/--color-ink:\s*#000000/);
  });

  test('no paper grid background', () => {
    expect(css).not.toMatch(/--paper-grid/);
  });
});
