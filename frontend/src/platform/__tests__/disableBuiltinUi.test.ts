/**
 * @file_name: disableBuiltinUi.test.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: A builtin reported disabled by the factory drops its whole UI row (teams pages) at boot; protected and enabled builtins keep theirs.
 */
import { Bot } from 'lucide-react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import '@/platform/builtin';
import '@/pages/settings/registerBuiltinSections';
import { disableBuiltinUi, loadPlugins } from '@/platform/loader';
import { CHANNELS, PAGES, PANELS, SETTINGS_SECTIONS, SIDEBAR, enableOwner } from '@/platform/registries';

vi.mock('@/stores/runtimeStore', () => ({ getApiBaseUrl: () => 'http://api' }));
vi.mock('@/lib/authHeaders', () => ({ getAuthHeaders: () => ({}) }));

const TEAM_PAGES = ['teams-new', 'team-detail', 'team-chat'];
const browserPanel = PANELS.get('browser')!;
const browserSection = SETTINGS_SECTIONS.get('browser')!;
const panelOwner = PANELS.ownerOf('browser');
const sectionOwner = SETTINGS_SECTIONS.ownerOf('browser');

afterEach(() => {
  vi.restoreAllMocks();
  enableOwner('builtin.browser');
  PANELS.register('browser', browserPanel, { owner: panelOwner, replace: true });
  SETTINGS_SECTIONS.register('browser', browserSection, { owner: sectionOwner, replace: true });
});

describe('disableBuiltinUi', () => {
  it('the browser panel and settings section belong to builtin.browser', () => {
    expect(PANELS.ownerOf('browser')).toBe('builtin.browser');
    expect(SETTINGS_SECTIONS.ownerOf('browser')).toBe('builtin.browser');
  });

  it('a disabled browser builtin removes its panel and settings through the plugin loader', async () => {
    const body = JSON.stringify({ data: { plugins: [], builtins: [
      { id: 'builtin.browser', enabled: false, excluded: false, protected: false },
    ] } });
    await loadPlugins({ fetchImpl: vi.fn(async () => new Response(body, { status: 200 })) });
    expect(PANELS.has('browser')).toBe(false);
    expect(SETTINGS_SECTIONS.has('browser')).toBe(false);
    expect(PANELS.has('jobs')).toBe(true);
    expect(SETTINGS_SECTIONS.has('providers')).toBe(true);

    // Late settings chunk registration must use the feature's owner too.
    SETTINGS_SECTIONS.register('browser', browserSection, { owner: sectionOwner, replace: true });
    expect(SETTINGS_SECTIONS.has('browser')).toBe(false);
    expect(disableBuiltinUi('builtin.browser')).toEqual([]);
  });

  it('a builtin the distribution excludes loses its UI even when it is protected', async () => {
    const body = JSON.stringify({ data: { plugins: [], builtins: [
      { id: 'builtin.browser', enabled: false, excluded: true, protected: true },
    ] } });
    await loadPlugins({ fetchImpl: vi.fn(async () => new Response(body, { status: 200 })) });
    expect(PANELS.has('browser')).toBe(false);
  });

  it('builtin.ts tags the teams pages with the builtin.teams owner', () => {
    for (const id of TEAM_PAGES) expect(PAGES.list().find((e) => e.id === id)?.owner).toBe('builtin.teams');
    expect(PAGES.list().find((e) => e.id === 'settings')?.owner).toBe('builtin.ui');
  });

  it('loadPlugins removes the UI row of a disabled builtin and leaves protected/enabled ones alone', async () => {
    const before = PAGES.ids().length;
    const body = JSON.stringify({
      data: {
        plugins: [],
        builtins: [
          { id: 'builtin.nexus_plugins_module', enabled: false, excluded: false, protected: true },
          { id: 'builtin.chat', enabled: true, excluded: false, protected: false },
          { id: 'builtin.teams', enabled: false, excluded: false, protected: false },
        ],
      },
    });
    const fetchImpl = vi.fn(async () => new Response(body, { status: 200 })) as unknown as typeof fetch;
    await loadPlugins({ fetchImpl });
    for (const id of TEAM_PAGES) expect(PAGES.has(id)).toBe(false);
    expect(PAGES.ids().length).toBe(before - TEAM_PAGES.length);
    expect(SIDEBAR.ids()).toContain('settings');
    expect(disableBuiltinUi('builtin.teams')).toEqual([]); // idempotent
  });

  it('blacklists the owner so a registration that has not happened YET is rejected too', () => {
    // This is the lazy-registration case `registerBuiltinChannels.ts` hits in production:
    // the channel row is only registered the first time its chunk mounts, which can be well
    // after `disableBuiltinUi` ran at boot. A one-shot `removeOwner` sweep at disable time
    // cannot purge a registration that has not happened yet.
    disableBuiltinUi('builtin.channels.ghost');
    CHANNELS.register('ghost', { label: 'Ghost', icon: Bot, component: () => null, fetchStatus: async () => 'unbound' }, { owner: 'builtin.channels.ghost' });
    expect(CHANNELS.has('ghost')).toBe(false);
  });
});
