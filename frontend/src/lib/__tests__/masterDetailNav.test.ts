/**
 * Pins the responsive master–detail nav (GitHub #130).
 *
 * A fixed w-56 (224px) rail with no breakpoint ate most of a 360px
 * viewport. Below md the rail must give up the fixed width and stack above
 * the content as a horizontal scroll strip; at md+ it is a 224px column.
 * SettingsPage and DashboardPage both render these exact strings (their
 * page tests assert that), so this is the single breakpoint pin.
 */
import { describe, expect, it } from 'vitest';
import {
  MASTER_DETAIL_ROW_CLASS,
  MASTER_NAV_CLASS,
  MASTER_NAV_ITEM_CLASS,
  masterNavItemClass,
} from '../masterDetailNav';

const tokens = (s: string) => s.split(/\s+/);

describe('masterDetailNav', () => {
  it('stacks vertically below md and side by side at md+', () => {
    expect(tokens(MASTER_DETAIL_ROW_CLASS)).toEqual(
      expect.arrayContaining(['flex-col', 'md:flex-row']),
    );
  });

  it('the rail has a fixed width only at md+ and scrolls horizontally below', () => {
    const nav = tokens(MASTER_NAV_CLASS);
    expect(nav).toContain('md:w-56');
    expect(nav).not.toContain('w-56');
    expect(nav).toContain('overflow-x-auto');
    expect(nav).toContain('shrink-0');
  });

  it('items keep their width in the strip and fill the column at md+', () => {
    const item = tokens(MASTER_NAV_ITEM_CLASS);
    expect(item).toContain('shrink-0');
    expect(item).toContain('md:w-full');
    expect(item).not.toContain('w-full');
  });

  it('merging the state colours keeps every base class (no tailwind-merge loss)', () => {
    for (const active of [true, false]) {
      expect(tokens(masterNavItemClass(active))).toEqual(
        expect.arrayContaining(tokens(MASTER_NAV_ITEM_CLASS)),
      );
    }
    expect(tokens(masterNavItemClass(true))).toContain('font-medium');
    expect(tokens(masterNavItemClass(false))).not.toContain('font-medium');
  });
});
