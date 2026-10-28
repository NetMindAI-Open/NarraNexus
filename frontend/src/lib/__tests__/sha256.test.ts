/**
 * @file_name: sha256.test.ts
 * @description: sha256Hex must produce the exact digest the backend computes
 * over the same bytes — it is the optimistic-lock token for user edits, so a
 * mismatch here turns every save into a phantom 409.
 */

import { describe, expect, it } from 'vitest';
import { sha256Hex } from '../sha256';

describe('sha256Hex', () => {
  it('matches the known vector for "abc"', async () => {
    expect(await sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  it('matches the empty-input vector', async () => {
    expect(await sha256Hex('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
  });

  it('hashes UTF-8 bytes, not UTF-16 code units', async () => {
    // sha256 of the UTF-8 encoding of "中文" — computed with hashlib.
    expect(await sha256Hex('中文')).toBe(
      '72726d8818f693066ceb69afa364218b692e62ea92b385782363780f47529c21',
    );
  });

  it('accepts an ArrayBuffer and hashes its bytes', async () => {
    const buf = new TextEncoder().encode('abc').buffer as ArrayBuffer;
    expect(await sha256Hex(buf)).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });
});
