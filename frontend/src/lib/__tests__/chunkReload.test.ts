/**
 * @file_name: chunkReload.test.ts
 * @description: Stale-chunk recovery primitives. `reloadOncePerSession` reloads
 * at most once per session (a broken build must not loop), and degrades safely
 * when storage is unavailable. `isChunkLoadError` tells a chunk 404 apart from a
 * real render bug.
 */
import { describe, expect, test, vi } from 'vitest';
import { isChunkLoadError, reloadOncePerSession } from '../chunkReload';

function memStorage(): Pick<Storage, 'getItem' | 'setItem'> {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
  } as Pick<Storage, 'getItem' | 'setItem'>;
}

describe('reloadOncePerSession', () => {
  test('reloads once on the first stale-chunk error', () => {
    const reload = vi.fn();
    expect(reloadOncePerSession(reload, memStorage())).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('does not reload again in the same session (no reload loop)', () => {
    const reload = vi.fn();
    const storage = memStorage();
    reloadOncePerSession(reload, storage);
    expect(reloadOncePerSession(reload, storage)).toBe(false);
    expect(reloadOncePerSession(reload, storage)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('does not reload when storage is unavailable (privacy mode)', () => {
    const reload = vi.fn();
    expect(reloadOncePerSession(reload, null)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('isChunkLoadError', () => {
  test('recognises dynamic-import / chunk failures', () => {
    expect(isChunkLoadError(new Error('Failed to fetch dynamically imported module: /x.js'))).toBe(true);
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new Error('Loading chunk 42 failed'))).toBe(true);
  });

  test('does NOT flag ordinary render bugs', () => {
    expect(isChunkLoadError(new Error("Cannot read properties of undefined (reading 'map')"))).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
  });
});
