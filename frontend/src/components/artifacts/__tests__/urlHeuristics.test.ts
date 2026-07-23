/**
 * @file_name: NewTabOmnibox.test.ts
 * @description: Tests for the omnibox URL-detection heuristics — the logic
 * that decides "open this as a URL" vs "filter existing artifacts".
 */
import { describe, expect, test } from 'vitest';
import { looksLikeUrl, normalizeUrl } from '../urlHeuristics';

describe('looksLikeUrl', () => {
  test.each([
    'https://example.com',
    'http://example.com/path?q=1',
    'example.com',
    'grafana.internal.corp/d/abc',
    'sub.domain.co.uk/page',
  ])('treats %s as a URL', (s) => {
    expect(looksLikeUrl(s)).toBe(true);
  });

  test.each([
    '',
    'sales report',            // has a space → search
    'grafana',                 // single word, no dot → search
    'my dashboard tab',
    'report.',                 // trailing dot, no tail
    'just text with spaces',
  ])('treats %s as a search query', (s) => {
    expect(looksLikeUrl(s)).toBe(false);
  });
});

describe('normalizeUrl', () => {
  test('leaves an explicit scheme untouched', () => {
    expect(normalizeUrl('http://x.com')).toBe('http://x.com');
    expect(normalizeUrl('https://x.com')).toBe('https://x.com');
  });
  test('prepends https to a bare host', () => {
    expect(normalizeUrl('example.com/path')).toBe('https://example.com/path');
  });
  test('trims whitespace', () => {
    expect(normalizeUrl('  example.com  ')).toBe('https://example.com');
  });
});
