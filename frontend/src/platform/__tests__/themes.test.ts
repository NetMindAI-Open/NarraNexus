/**
 * @file_name: themes.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Themes may only override declared tokens; apply/clear touch exactly those variables.
 */
import { describe, expect, it } from 'vitest';

import { THEMES, applyTheme, clearTheme, validateThemeTokens } from '@/platform/registries';
import { THEME_TOKENS } from '@/platform/registries/themeTokens.generated';

describe('theme registry', () => {
  it('rejects unknown tokens and unsafe values', () => {
    expect(validateThemeTokens({ '--nm-ink': '#111' })).toEqual([]);
    expect(validateThemeTokens({ '--evil': 'x' })[0]).toMatch(/unknown token/);
    expect(validateThemeTokens({ '--nm-ink': 'url(https://x)' })[0]).toMatch(/unsafe/);
    expect(() => THEMES.register('acme.bad', { displayName: 'Bad', tokens: { '--nope': '1' } })).toThrow(/unknown token/);
    expect(THEME_TOKENS).toContain('--nm-ink');
  });

  it('rejects url() case-insensitively and with internal whitespace (M-4)', () => {
    // No colon needed for a protocol-relative resource load — `//host/path` stays inside
    // SAFE_VALUE's allowed character set, so an uppercase/whitespace-varied "url(" was the only
    // thing standing between a plugin theme and a same-request tracking-pixel/exfil load.
    expect(validateThemeTokens({ '--nm-ink': 'URL(//evil.example/pixel.png)' })[0]).toMatch(/unsafe/);
    expect(validateThemeTokens({ '--nm-ink': 'Url(//evil.example/pixel.png)' })[0]).toMatch(/unsafe/);
    expect(validateThemeTokens({ '--nm-ink': 'url (//evil.example/pixel.png)' })[0]).toMatch(/unsafe/);
  });

  it('applies and clears exactly the theme tokens, and disposes cleanly (M-12)', () => {
    const root = document.createElement('div');
    const dispose = THEMES.register('acme.dark', { displayName: 'Acme', tokens: { '--nm-ink': '#fff', '--nm-paper': '#000' }, dark: true }, { owner: 'acme.theme' });
    applyTheme('acme.dark', root);
    expect(root.style.getPropertyValue('--nm-ink')).toBe('#fff');
    expect(root.dataset.pluginTheme).toBe('acme.dark');
    clearTheme(root);
    expect(root.style.getPropertyValue('--nm-ink')).toBe('');
    expect(root.dataset.pluginTheme).toBeUndefined();
    // Architecture E3(b): `applyTheme` uses `THEMES.getOrThrow(id)` now instead of hand-rolling
    // the same "id must exist" throw `Registry.get` + a manual `if (!x) throw` used to duplicate.
    expect(() => applyTheme('nope', root)).toThrow(/unknown entry "nope"/);
    // The registry is module-level and shared across every test in this file (and, without
    // vitest's module isolation, potentially other suites too) — leaving 'acme.dark' registered
    // would make a second run of this same test (or any other suite reusing that id) throw
    // RegistryConflictError instead of exercising the behavior under test.
    dispose();
    expect(THEMES.has('acme.dark')).toBe(false);
  });
});
