/**
 * @file_name: themes.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Theme registry — a theme overrides design tokens the shell declared in `@theme`, nothing else.
 *
 * A plugin theme is data: token → value. Registration validates every key
 * against the generated token list (`themeTokens.generated.ts`, produced
 * from `index.css`), so a theme cannot invent variables the components do
 * not read or smuggle arbitrary CSS. `applyTheme` sets the variables on the
 * root element; `clearTheme` removes exactly what was set.
 */
import { Registry } from './registry';
import { THEME_TOKENS } from './themeTokens.generated';

export interface ThemeDef {
  displayName: string;
  tokens: Record<string, string>;
  dark?: boolean;
}

const KNOWN = new Set<string>(THEME_TOKENS);
const SAFE_VALUE = /^[\w\s#%.,()/+\-"']*$/;
// `url(` is checked case-insensitively with optional whitespace before the paren: CSS parses
// `URL(...)` / `Url (...)` identically to `url(...)`, and a protocol-relative resource
// (`//host/path`, no colon) stays entirely inside SAFE_VALUE's allowed character set — so a
// case- or whitespace-sensitive check on its own was a same-request tracking-pixel/exfil hole a
// plugin theme's token value could walk straight through (M-4).
const HAS_URL_FUNCTION = /url\s*\(/i;

export function validateThemeTokens(tokens: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const [key, value] of Object.entries(tokens)) {
    if (!KNOWN.has(key)) problems.push(`unknown token ${key}`);
    else if (!SAFE_VALUE.test(value) || HAS_URL_FUNCTION.test(value)) problems.push(`unsafe value for ${key}`);
  }
  return problems;
}

export const THEMES = new Registry<ThemeDef>('ui.themes', {
  validate: (value) => {
    const problems = validateThemeTokens(value.tokens);
    if (problems.length) throw new Error(`ui.themes: ${problems.join('; ')}`);
  },
});

let applied: string[] = [];

export function applyTheme(id: string, root: HTMLElement = document.documentElement): void {
  // A theme id reaching here MUST already be registered — `getOrThrow` (architecture E3(b))
  // reports the missing entry as an error instead of this call site hand-rolling the same
  // `get()` + `if (!x) throw` every "id must exist" lookup used to duplicate.
  const theme = THEMES.getOrThrow(id);
  clearTheme(root);
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value);
    applied.push(key);
  }
  root.dataset.pluginTheme = id;
}

export function clearTheme(root: HTMLElement = document.documentElement): void {
  for (const key of applied) root.style.removeProperty(key);
  applied = [];
  delete root.dataset.pluginTheme;
}
