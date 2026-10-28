/**
 * @file_name: panelsStrip.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The drawer strip is derived from `PANELS`' `strip` metadata — a plugin registering a panel with `strip` gets a real strip entry, not just a reachable-by-direct-link panel.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { Puzzle } from 'lucide-react';

import '@/platform/builtin';
import { PANELS } from '@/platform/registries';
import { allTabs, builtinTabIds, stripCategories } from '../tabs';

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('stripCategories() / allTabs() / builtinTabIds()', () => {
  it('every builtin panel with strip metadata appears exactly once, grouped by its category', () => {
    const categories = stripCategories();
    const configIds = categories.find((c) => c.labelKey === 'rail.category.config')!.tabs.map((t) => t.id);
    expect(configIds).toEqual(['builder', 'awareness', 'workspace', 'channels', 'smarthome']);
    const activityIds = categories.find((c) => c.labelKey === 'rail.category.activity')!.tabs.map((t) => t.id);
    // 'browser' is order 25, so it lands after inbox (20) and before
    // artifacts (30): the live browser is something the agent is DOING, not a
    // configuration surface.
    expect(activityIds).toEqual(['jobs', 'inbox', 'browser', 'artifacts']);
  });

  it('builtinTabIds() lists every builtin (owner builtin.ui) strip tab', () => {
    const ids = builtinTabIds();
    for (const id of ['builder', 'awareness', 'workspace', 'channels', 'smarthome', 'jobs', 'inbox', 'artifacts', 'memory', 'social', 'skills', 'mcp']) {
      expect(ids).toContain(id);
    }
    // Browser is still on the strip, but its feature plugin owns its lifecycle.
    expect(ids).not.toContain('browser');
    expect(PANELS.ownerOf('browser')).toBe('builtin.browser');
  });

  it('a plugin panel registered WITH strip metadata gets a real strip entry — the I-3 fix', () => {
    disposers.push(
      PANELS.register(
        'acme.widget',
        { component: () => null, strip: { label: 'Widget', labelKey: 'plugin:acme.widget', icon: Puzzle, category: 'skills', order: 90 } },
        { owner: 'acme.plugin' },
      ),
    );
    expect(allTabs().some((t) => t.id === 'acme.widget')).toBe(true);
    const skills = stripCategories().find((c) => c.labelKey === 'rail.category.skills')!;
    expect(skills.tabs.map((t) => t.id)).toEqual(['skills', 'mcp', 'acme.widget']); // sorted by order after the two builtins
    // it is NOT counted as a builtin tab (owner is the plugin, not builtin.ui)
    expect(builtinTabIds()).not.toContain('acme.widget');
  });

  it('a panel registered WITHOUT strip metadata never appears on the strip (reachable only by direct id)', () => {
    disposers.push(PANELS.register('acme.hidden', { component: () => null }, { owner: 'acme.plugin' }));
    expect(allTabs().some((t) => t.id === 'acme.hidden')).toBe(false);
  });

  it('an unknown category falls back to config rather than being dropped', () => {
    disposers.push(
      PANELS.register(
        'acme.orphan',
        { component: () => null, strip: { label: 'Orphan', labelKey: 'plugin:acme.orphan', icon: Puzzle, category: 'not-a-real-category' } },
        { owner: 'acme.plugin' },
      ),
    );
    const config = stripCategories().find((c) => c.labelKey === 'rail.category.config')!;
    expect(config.tabs.some((t) => t.id === 'acme.orphan')).toBe(true);
  });
});
