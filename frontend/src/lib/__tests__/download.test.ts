/**
 * @file_name: download.test.ts
 * @description: Contract tests for downloadFile's cross-surface behaviour.
 *
 * The contract that matters is SYMMETRY. Before 2026-07-30 the Tauri branch
 * caught its own error, showed a native alert and returned normally, while the
 * browser branch threw. Two consequences, both invisible in CI:
 *   - wry does not render window.alert, so a failed desktop download said
 *     nothing at all;
 *   - a caller's `.catch()` was therefore dead code on desktop while the very
 *     same `.catch()` worked in the browser.
 * These tests pin "throws on both surfaces" and the saved-path return value that
 * lets a caller tell the user where a desktop download went (there is no
 * download shelf in the app).
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const mockIsTauri = vi.fn(() => false);
const mockDownloadViaTauri = vi.fn(async () => '/Users/x/Downloads/f.pdf');
vi.mock('../tauri', () => ({
  isTauri: () => mockIsTauri(),
  downloadFileViaTauri: (...a: unknown[]) => mockDownloadViaTauri(...(a as [])),
}));

import { downloadFile } from '../download';

const OPTS = { url: 'https://api.test/f.pdf', filename: 'f.pdf' };

beforeEach(() => {
  mockIsTauri.mockReset();
  mockIsTauri.mockReturnValue(false);
  mockDownloadViaTauri.mockReset();
  mockDownloadViaTauri.mockResolvedValue('/Users/x/Downloads/f.pdf');
});

afterEach(() => {
  vi.restoreAllMocks();
  // restoreAllMocks does NOT undo stubGlobal, and the browser-surface tests
  // replace `URL` with a plain object that is not a constructor — leaving that
  // in place is a trap for the next test added to this file.
  vi.unstubAllGlobals();
});

describe('downloadFile — desktop surface', () => {
  beforeEach(() => mockIsTauri.mockReturnValue(true));

  test('returns the saved path so the caller can say where the file went', async () => {
    await expect(downloadFile(OPTS)).resolves.toBe('/Users/x/Downloads/f.pdf');
  });

  test('THROWS instead of swallowing + alerting (the desktop-silence bug)', async () => {
    mockDownloadViaTauri.mockRejectedValueOnce(new Error('disk full'));
    const nativeAlert = vi.spyOn(window, 'alert');
    await expect(downloadFile(OPTS)).rejects.toThrow('disk full');
    // The old code alerted here and resolved, so the caller never learned.
    expect(nativeAlert).not.toHaveBeenCalled();
  });

  test('a null path (isTauri flipped mid-flight) resolves without inventing one', async () => {
    mockDownloadViaTauri.mockResolvedValueOnce(null as unknown as string);
    await expect(downloadFile(OPTS)).resolves.toBeNull();
  });
});

describe('downloadFile — browser surface', () => {
  test('returns null: the download manager owns the file, there is no path', async () => {
    // Hand-rolled response: jsdom's Response/Blob interop breaks on .blob() —
    // an environment artefact, nothing to do with the code under test.
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob(['x']) })));
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} });
    await expect(downloadFile(OPTS)).resolves.toBeNull();
  });

  test('throws on a non-ok response — same shape as the desktop surface', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 403, statusText: 'Forbidden' })),
    );
    const nativeAlert = vi.spyOn(window, 'alert');
    await expect(downloadFile(OPTS)).rejects.toThrow(/403/);
    expect(nativeAlert).not.toHaveBeenCalled();
  });
});
