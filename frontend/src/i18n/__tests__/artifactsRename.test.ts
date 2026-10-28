/**
 * @file_name: artifactsRename.test.ts
 * @description: Regression guard for req #3 — the Chinese artifacts term is
 * "可视化产物", never the old "制品". Any reintroduced 制品 turns this red.
 */
import { describe, it, expect } from 'vitest';
import zhJson from '@/i18n/locales/zh.json';

const zh = zhJson as unknown as { rail: { artifacts: string } };

describe('req #3 artifacts term rename (zh)', () => {
  it('rail.artifacts label uses 可视化产物', () => {
    expect(zh.rail.artifacts).toBe('可视化产物');
  });

  it('no 制品 remains anywhere in the zh bundle', () => {
    expect(JSON.stringify(zhJson).includes('制品')).toBe(false);
  });
});
