/**
 * @file_name: drafts.test.ts
 * @description: The draft layer's failure honesty (review #334 r3 I3): a
 * rejected write must INVALIDATE the previous draft — restoring an older
 * text under a "your unsaved changes" banner is worse than losing it.
 *
 * The quota case installs its OWN Storage fake (r4 C1): spying on the
 * global localStorage works only in environments where test-setup's
 * in-memory shim replaced Node's broken global — CI's jsdom ships a real
 * Storage whose methods a spy cannot reach, so the spy version was green
 * locally and red in CI. The fake mirrors test-setup's proven shape and is
 * restored from the saved descriptor after each test (vitest reuses
 * globalThis across files in a worker — a leaked fake would poison every
 * other suite's beforeEach(localStorage.clear)).
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readDraft, writeDraft } from '../drafts';

beforeEach(() => localStorage.clear());

const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');

function installThrowingStorage(throwOnKeySubstring: string): Record<string, string> {
  const store: Record<string, string> = {};
  const fake = {
    get length() { return Object.keys(store).length; },
    // In-place clear (r5 M6): rebinding `store` would strand the reference
    // this function RETURNED — a later case seeding after a clear() would
    // write into an object the fake no longer reads.
    clear() { for (const k of Object.keys(store)) delete store[k]; },
    getItem(k: string) {
      return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
    },
    setItem(k: string, v: string) {
      if (k.includes(throwOnKeySubstring)) {
        throw new DOMException('quota', 'QuotaExceededError');
      }
      store[k] = String(v);
    },
    removeItem(k: string) { delete store[k]; },
    key(i: number) { return Object.keys(store)[i] ?? null; },
  } as unknown as Storage;
  Object.defineProperty(globalThis, 'localStorage', {
    value: fake, configurable: true, writable: true,
  });
  return store;
}

afterEach(() => {
  if (originalDescriptor) {
    Object.defineProperty(globalThis, 'localStorage', originalDescriptor);
  } else {
    // No descriptor to restore means localStorage was NOT an own property
    // of globalThis in this environment (r5 M5) — explicitly remove the
    // fake so lookups fall back to wherever the environment defines it,
    // instead of the fake silently swallowing every later suite's storage.
    delete (globalThis as unknown as Record<string, unknown>).localStorage;
  }
});

describe('writeDraft failure paths', () => {
  it('an oversize write removes the previous draft', () => {
    // Real storage on purpose: this case verifies removeItem semantics on
    // the actual environment (r4 C1 trap ③).
    expect(writeDraft('a1', { text: 'small', baseHash: 'h' })).toBe(true);
    expect(readDraft('a1')?.text).toBe('small');
    expect(writeDraft('a1', { text: 'x'.repeat(600 * 1024), baseHash: 'h' })).toBe(false);
    expect(readDraft('a1')).toBeNull();
  });

  it('a quota failure removes the previous draft', () => {
    const store = installThrowingStorage('a2-quota');
    // seed the "previous" draft directly in the fake's store (setItem for
    // this key throws by design)
    store['narra:artifact-draft:a2-quota'] = JSON.stringify({ text: 'old', baseHash: 'h' });
    expect(readDraft('a2-quota')?.text).toBe('old');

    expect(writeDraft('a2-quota', { text: 'newer', baseHash: 'h' })).toBe(false);
    // the stale draft is INVALIDATED — the next mount must not restore
    // "old" under a your-unsaved-changes banner
    expect(readDraft('a2-quota')).toBeNull();
  });
});
