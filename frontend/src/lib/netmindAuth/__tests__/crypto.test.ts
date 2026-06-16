import { describe, expect, test } from 'vitest';
import { encryptPassword, generateRandomString } from '../crypto';

describe('netmindAuth crypto', () => {
  test('DES-CBC encrypts to deterministic hex for a fixed key', () => {
    // key='01234567' (8 bytes, also the IV); PKCS7; CBC; hex ciphertext.
    // Golden vector computed with the same CryptoJS config Arena ships.
    expect(encryptPassword('hello', '01234567')).toBe('a96ba2b76b377060');
  });

  test('same message + same signStr key is stable', () => {
    const a = encryptPassword('123123aA!', 'abcd1234');
    const b = encryptPassword('123123aA!', 'abcd1234');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]+$/);
  });

  test('generateRandomString length + charset', () => {
    const s = generateRandomString(8);
    expect(s).toHaveLength(8);
    expect(s).toMatch(/^[a-zA-Z0-9]+$/);
  });
});
