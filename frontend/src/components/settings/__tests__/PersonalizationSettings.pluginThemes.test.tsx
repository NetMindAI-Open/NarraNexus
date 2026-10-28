/**
 * @file_name: PersonalizationSettings.pluginThemes.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-08
 * @description: A plugin theme (ui.themes) is selectable in Settings → Personalization and its tokens land on the document root.
 *
 * Found by the 2026-09-08 local end-to-end pass: themes registered fine and
 * were never reachable — no UI listed them, `applyTheme` had no caller.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { THEMES } from '@/platform/registries';
import { usePluginTheme } from '@/hooks/usePluginTheme';
import { useThemeStore } from '@/stores/themeStore';
import { PersonalizationSettings } from '../PersonalizationSettings';

function Harness() {
  usePluginTheme();
  return <PersonalizationSettings />;
}

let dispose: (() => void) | null = null;
beforeEach(() => {
  useThemeStore.setState({ theme: 'light', effectiveTheme: 'light', pluginTheme: null });
});
afterEach(() => {
  dispose?.();
  dispose = null;
  document.documentElement.style.removeProperty('--nm-ink');
});

describe('PersonalizationSettings — plugin themes', () => {
  test('no picker while no plugin theme is registered', () => {
    render(<Harness />);
    expect(screen.queryByTestId('plugin-theme-none')).toBeNull();
  });

  test('a registered theme is listed, applies its tokens on click, follows its dark flag, and "shell default" clears it', () => {
    dispose = THEMES.register('acme.night', { displayName: 'Acme Night', tokens: { '--nm-ink': '#eeeeee' }, dark: true }, { owner: 'acme.plugin' });
    render(<Harness />);
    fireEvent.click(screen.getByTestId('plugin-theme-acme.night'));
    expect(useThemeStore.getState().pluginTheme).toBe('acme.night');
    expect(useThemeStore.getState().theme).toBe('dark');
    expect(document.documentElement.style.getPropertyValue('--nm-ink')).toBe('#eeeeee');
    expect(document.documentElement.dataset.pluginTheme).toBe('acme.night');
    fireEvent.click(screen.getByTestId('plugin-theme-none'));
    expect(useThemeStore.getState().pluginTheme).toBeNull();
    expect(document.documentElement.style.getPropertyValue('--nm-ink')).toBe('');
  });

  test('a persisted choice is applied the moment its plugin registers the theme (late activation)', () => {
    useThemeStore.setState({ pluginTheme: 'acme.late' });
    render(<Harness />);
    expect(document.documentElement.style.getPropertyValue('--nm-ink')).toBe('');
    act(() => {
      dispose = THEMES.register('acme.late', { displayName: 'Acme Late', tokens: { '--nm-ink': '#abcdef' } }, { owner: 'acme.plugin' });
    });
    expect(document.documentElement.style.getPropertyValue('--nm-ink')).toBe('#abcdef');
    expect(screen.getByTestId('plugin-theme-acme.late').getAttribute('aria-checked')).toBe('true');
  });
});
