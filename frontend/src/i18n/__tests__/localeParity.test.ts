/**
 * i18n parity gate. en.json is the reference key set; every other locale is
 * measured against it, per NAMESPACE:
 *
 * Namespaces this codebase has fully localized must STAY complete — a new
 * key added to en without its nine siblings fails HERE, in the namespace
 * the author touched, instead of surfacing as one English row inside a
 * localized menu.
 *
 * Deliberately NOT a global missing-key ceiling: a hardcoded total turns
 * every en-only key added by an unrelated PR into a red light with the
 * wrong name on it (the count is branch-relative, so the PR that added the
 * key stays green and the next PR to merge goes red). Per-namespace
 * assertions keep the blame with the change that earned it. The historical
 * backlog (~430 en keys per non-en/zh locale, i18next falls back to
 * English) is a separate backfill effort; grow this list as namespaces get
 * completed.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

import en from '../locales/en.json';
import zh from '../locales/zh.json';
import ar from '../locales/ar.json';
import de from '../locales/de.json';
import es from '../locales/es.json';
import fr from '../locales/fr.json';
import ja from '../locales/ja.json';
import ko from '../locales/ko.json';
import pt from '../locales/pt.json';
import ru from '../locales/ru.json';

const LOCALES: Record<string, unknown> = { zh, ar, de, es, fr, ja, ko, pt, ru };

// Namespaces every locale must carry in full. Add a namespace here the
// moment it reaches 10/10 — that is what keeps it complete.
const COMPLETE_NAMESPACES = [
  'layout.teamRowMenu',
  'layout.agentRowMenu',
  'layout.createMenu',
  'chat.team.workspace',
  'chat.header',
  'bookmarks.coach',
  'bookmarks.drawer',
  'pages.settings.nav',
  'pages.settings.personalization',
  'pages.manageAgents',
  'pages.agentProfile',
  'pages.welcome',
  'common',
  'appBanners',
  'dashboard.banners',
  'jobs.action',
  'jobs.editPayload',
  'browser',
  'settings.browser',
  'pages.settings.browser',
];

// zh is the co-source locale: it must mirror en in FULL, so a new en key
// missing its zh twin fails regardless of namespace.
const FULL_PARITY_LOCALES = ['zh'];

function leaves(obj: unknown, prefix = ''): string[] {
  if (typeof obj !== 'object' || obj === null) return [prefix];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    leaves(v, prefix ? `${prefix}.${k}` : k),
  );
}

const enLeaves = new Set(leaves(en));

const browserUiKeys = new Set<string>();
for (const path of [
  '../../components/artifacts/renderers/BrowserStreamPanel.tsx',
  '../../components/artifacts/renderers/BrowserPageTabs.tsx',
  '../../components/layout/BrowserNotices.tsx',
  '../../components/layout/BrowserLoginNotice.tsx',
  '../../components/settings/BrowserSettings.tsx',
  '../../components/settings/BrowserManualInstall.tsx',
  '../../components/settings/BrowserScriptPermissions.tsx',
  '../../components/chat/ChatHeader.tsx',
  '../../components/layout/MainLayout.tsx',
  '../../pages/settings/sections.tsx',
]) {
  const source = ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)
      && ['t', 'tr'].includes(node.expression.text)) {
      const key = node.arguments[0];
      if (key && ts.isStringLiteral(key) && /^(browser\.|settings\.browser\.|pages\.settings\.browser\.|rail\.browser$)/.test(key.text)) {
        browserUiKeys.add(key.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

function valueAt(json: unknown, key: string): unknown {
  return key.split('.').reduce<unknown>((value, part) =>
    typeof value === 'object' && value !== null ? (value as Record<string, unknown>)[part] : undefined, json);
}

describe('browser UI translations', () => {
  for (const [locale, json] of Object.entries({ en, ...LOCALES })) {
    it(`${locale}: every browser key used by the UI has a nonempty translation`, () => {
      expect(browserUiKeys.has('browser.notices.loadFailed')).toBe(true);
      const missing = [...browserUiKeys].filter((key) => {
        const value = valueAt(json, key);
        return typeof value !== 'string' || !value.trim();
      });
      expect(missing).toEqual([]);
    });

    it(`${locale}: browser interpolation variables match English`, () => {
      const variables = (value: unknown) => typeof value === 'string'
        ? [...value.matchAll(/\{\{([^}]+)\}\}/g)].map((match) => match[1]).sort() : [];
      for (const key of browserUiKeys) {
        expect(variables(valueAt(json, key)), key).toEqual(variables(valueAt(en, key)));
      }
    });
  }
});

describe('locale parity with en', () => {
  for (const [locale, json] of Object.entries(LOCALES)) {
    const have = new Set(leaves(json));
    const missing = [...enLeaves].filter((k) => !have.has(k));

    it(`${locale}: fully-localized namespaces stay complete`, () => {
      const holes = missing.filter((k) =>
        COMPLETE_NAMESPACES.some((ns) => k.startsWith(`${ns}.`)),
      );
      expect(holes).toEqual([]);
    });

    if (FULL_PARITY_LOCALES.includes(locale)) {
      it(`${locale}: mirrors en in full (co-source locale)`, () => {
        expect(missing).toEqual([]);
      });
    }
  }
});
