/**
 * @file_name: errorSink.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: UI errors are attributed to the shell or a plugin and delivered to subscribers.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { attributeChunkUrl, onUiError, recentUiErrors, reportUiError, resetErrorSink } from '@/platform/errorSink';

afterEach(() => resetErrorSink());

describe('error sink', () => {
  it('attributes by chunk url and notifies listeners', () => {
    attributeChunkUrl('plugin://acme.weather/', 'acme.weather');
    const seen = vi.fn();
    onUiError(seen);
    const err = new Error('boom');
    err.stack = 'Error: boom\n    at render (plugin://acme.weather/plugin.js:1:1)';
    const report = reportUiError(err, { kind: 'render' });
    expect(report.source).toBe('acme.weather');
    expect(seen).toHaveBeenCalledWith(report);
    expect(reportUiError(new Error('shell')).source).toBe('shell');
    expect(recentUiErrors()).toHaveLength(2);
  });

  it('a report raised from inside a report is delivered after it, not recursively and not dropped', () => {
    const seen: string[] = [];
    onUiError((r) => {
      seen.push(r.error.message);
      if (seen.length < 3) reportUiError(new Error(`nested-${seen.length}`));
    });
    reportUiError(new Error('outer'));
    // Delivered in order, each one after the previous finished (no re-entrant stack).
    expect(seen).toEqual(['outer', 'nested-1', 'nested-2']);
    expect(recentUiErrors().map((r) => r.error.message)).toEqual(['outer', 'nested-1', 'nested-2']);
  });

  it('a listener that reports on every report is cut off at the bound instead of looping forever', () => {
    let calls = 0;
    onUiError(() => {
      calls += 1;
      reportUiError(new Error('again'));
    });
    reportUiError(new Error('outer'));
    expect(calls).toBeGreaterThan(1);
    expect(calls).toBeLessThanOrEqual(51); // the outer report + at most RECENT_LIMIT drained ones
  });

  it('recentUiErrors returns a snapshot, not the live buffer', () => {
    reportUiError(new Error('one'));
    const snapshot = recentUiErrors();
    reportUiError(new Error('two'));
    expect(snapshot).toHaveLength(1);
    expect(recentUiErrors()).toHaveLength(2);
  });

  it('a throwing listener does not mask the report', () => {
    onUiError(() => {
      throw new Error('listener broke');
    });
    expect(() => reportUiError(new Error('x'))).not.toThrow();
  });
});
