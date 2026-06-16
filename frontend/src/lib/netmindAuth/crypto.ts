/**
 * @file_name: crypto.ts
 * @description: NetMind login password encryption. DES-CBC with the
 * signStr as both key and IV, PKCS7, hex output — the exact protocol
 * NetMind's emailLogin expects (ported verbatim from Arena's client;
 * Web Crypto cannot do DES, hence crypto-js).
 */
import CryptoJS from 'crypto-js';

/** Random alphanumeric string; default 8 chars. Used as the DES key/IV. */
export function generateRandomString(length = 8): string {
  const charset =
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let out = '';
  for (let i = 0; i < length; i++) {
    out += charset[Math.floor(Math.random() * charset.length)];
  }
  return out;
}

/** DES-CBC encrypt `message` with `key` (key === IV), PKCS7, hex output. */
export function encryptPassword(message: string, key = '01234567'): string {
  const keyHex = CryptoJS.enc.Utf8.parse(key);
  const encrypted = CryptoJS.DES.encrypt(message, keyHex, {
    iv: keyHex,
    mode: CryptoJS.mode.CBC,
    padding: CryptoJS.pad.Pkcs7,
  });
  return encrypted.ciphertext.toString(CryptoJS.enc.Hex);
}
