/**
 * @file_name: htmlAnchorReplace.test.ts
 * @description: The anchored literal replace behind html per-element editing
 * (spec A §3.2): the anchor is the element's ORIGINAL innerHTML; it must
 * match the source exactly once. Zero matches = JS-generated content
 * (degrade to AI); multiple matches = widen to the element's outerHTML and
 * replace inside that unique occurrence; still ambiguous = degrade. NEVER
 * a nearest-guess replacement — a wrong-spot edit is worse than a refusal.
 */

import { describe, expect, it } from 'vitest';
import { applyBridgeEdit } from '../htmlAnchorReplace';

const PAGE = [
  '<html><body>',
  '<h1>Quarterly Report</h1>',
  '<p class="intro">Welcome</p>',
  '<p class="a">repeated</p>',
  '<p class="b">repeated</p>',
  '<script>document.title = "x"</script>',
  '</body></html>',
].join('\n');

describe('applyBridgeEdit', () => {
  it('replaces a unique inner anchor', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Quarterly Report',
      innerAfter: 'Q3 Report',
      outerBefore: '<h1>Quarterly Report</h1>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.result).toContain('<h1>Q3 Report</h1>');
      expect(r.result).not.toContain('Quarterly Report');
    }
  });

  it('widens to the outer anchor when the inner text is ambiguous', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'repeated',
      innerAfter: 'edited once',
      outerBefore: '<p class="a">repeated</p>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.result).toContain('<p class="a">edited once</p>');
      expect(r.result).toContain('<p class="b">repeated</p>'); // untouched
    }
  });

  it('refuses when the inner anchor is not in the source (JS-generated)', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'rendered by javascript',
      innerAfter: 'changed',
      outerBefore: '<div>rendered by javascript</div>',
    });
    expect(r).toEqual({ ok: false, reason: 'not-found' });
  });

  it('refuses when both anchors are ambiguous', () => {
    const page = '<p>x</p><p>x</p>';
    const r = applyBridgeEdit(page, {
      innerBefore: 'x',
      innerAfter: 'y',
      outerBefore: '<p>x</p>',
    });
    expect(r).toEqual({ ok: false, reason: 'ambiguous' });
  });

  it('replacement may contain <br> and inline format tags', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Welcome',
      innerAfter: 'Welcome<br><strong>back</strong>',
      outerBefore: '<p class="intro">Welcome</p>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.result).toContain('<p class="intro">Welcome<br><strong>back</strong></p>');
  });

  it('no-op edits are refused (nothing to save)', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Welcome',
      innerAfter: 'Welcome',
      outerBefore: '<p class="intro">Welcome</p>',
    });
    expect(r).toEqual({ ok: false, reason: 'no-change' });
  });
});

describe('anchor priority (review #334 I5): outer first, attribute-safe fallback', () => {
  it('a text unique only inside an attribute is refused, not written into alt', () => {
    // The visible "Total revenue" is script-generated (not in source); the
    // only literal occurrence is the alt attribute. Inner-first matching
    // would silently rewrite alt while the visible text stays stale.
    const page = '<div id="kpi"></div><img alt="Total revenue" src="c.png">';
    const r = applyBridgeEdit(page, {
      innerBefore: 'Total revenue',
      innerAfter: 'Total revenue (Q3)',
      outerBefore: '<span>Total revenue</span>', // script-made, not in source
    });
    expect(r).toEqual({ ok: false, reason: 'not-found' });
  });

  it('outer anchor wins even when the inner text also appears in an attribute', () => {
    const page = '<h1 title="Report">Report</h1>';
    const r = applyBridgeEdit(page, {
      innerBefore: 'Report',
      innerAfter: 'Q3 Report',
      outerBefore: '<h1 title="Report">Report</h1>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.result).toBe('<h1 title="Report">Q3 Report</h1>');
  });

  it('inner fallback only fires at a text position (preceded by ">")', () => {
    // outer doesn't match source (serializer differences are the norm), the
    // inner IS at a text position → allowed.
    const page = '<p class="x" >Fix me</p>';
    const r = applyBridgeEdit(page, {
      innerBefore: 'Fix me',
      innerAfter: 'Fixed',
      outerBefore: '<p class="x">Fix me</p>', // attr spacing differs
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.result).toContain('>Fixed</p>');
  });
});
