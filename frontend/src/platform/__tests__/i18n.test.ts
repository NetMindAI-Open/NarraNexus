/**
 * @file_name: i18n.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Plugin strings live in their own namespace and can be removed wholesale.
 */
import i18n from 'i18next';
import { describe, expect, it } from 'vitest';

import { addPluginBundle, pluginNamespace, pluginT, removePluginBundles } from '@/platform/i18n';

describe('plugin i18n namespace', () => {
  it('adds, resolves and removes a namespaced bundle without touching shell keys', () => {
    addPluginBundle('acme.weather', 'en', { title: 'Weather', nested: { deep: 'ok' } });
    expect(pluginT('acme.weather', 'title')).toBe('Weather');
    expect(pluginT('acme.weather', 'nested.deep')).toBe('ok');
    expect(i18n.t('title')).toBe('title'); // shell namespace untouched
    expect(pluginT('acme.weather', 'missing')).toBe('missing');
    // A key containing i18next's namespace separator is looked up verbatim.
    addPluginBundle('acme.weather', 'en', { 'unit:celsius': '°C' });
    expect(pluginT('acme.weather', 'unit:celsius')).toBe('°C');
    removePluginBundles('acme.weather');
    expect(i18n.hasResourceBundle('en', pluginNamespace('acme.weather'))).toBe(false);
  });
});
