/**
 * @file platform.detect.test.ts
 * @description Regression for the 2026-07-22 DMG bug: the packaged Tauri v2
 * desktop app showed "Not available in web mode" on the System page because
 * detectPlatform() keyed on `window.__TAURI__` — a global that v2 only injects
 * when `app.withGlobalTauri` is true (it is NOT set). The fix routes detection
 * through the shared isTauri() (which also checks `__TAURI_INTERNALS__`, always
 * present in v2). These tests pin that detectPlatform() follows isTauri().
 */
import { vi, test, expect, beforeEach } from 'vitest';

vi.mock('@/lib/tauri', () => ({ isTauri: vi.fn(), invokeTauri: vi.fn() }));

import { isTauri } from '@/lib/tauri';
import { detectPlatform } from '../platform';

const mockIsTauri = isTauri as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockIsTauri.mockReset();
});

test('isTauri() true → desktop bridge (service management available)', () => {
  mockIsTauri.mockReturnValue(true);
  expect(detectPlatform().isLocalMode()).toBe(true);
});

test('isTauri() false → web bridge (service management unavailable)', async () => {
  mockIsTauri.mockReturnValue(false);
  const p = detectPlatform();
  expect(p.isLocalMode()).toBe(false);
  await expect(p.getServiceStatus()).rejects.toThrow('Not available in web mode');
});
